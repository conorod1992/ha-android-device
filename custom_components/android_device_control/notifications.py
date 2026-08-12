"""Friendly notifications and bounded acknowledgement sessions."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant

from .commands import payload
from .const import (
    ACK_NOTIFICATION_ACTION_PREFIX,
    DATA_NOTIFICATION_MANAGER,
    DOMAIN,
    EVENT_MOBILE_APP_NOTIFICATION_ACTION,
    EVENT_NOTIFICATION_ACKNOWLEDGED,
    EVENT_NOTIFICATION_ACTION,
    NOTIFICATION_ACTION_PREFIX,
)
from .device import AndroidTarget

_LOGGER = logging.getLogger(__name__)

SendNotification = Callable[[AndroidTarget, dict[str, Any]], Awaitable[None]]
ACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_PROMPT_ACTIONS = 3
MAX_PROMPT_SESSIONS = 100
MAX_ACTION_TITLE_LENGTH = 80


def notification_payload(
    data: dict[str, Any], *, urgent: bool = False
) -> dict[str, Any]:
    """Build the curated, documented Companion notification subset."""
    result: dict[str, Any] = {"message": data["message"]}
    if title := data.get("title", "").strip():
        result["title"] = title
    notification_data: dict[str, Any] = {}
    for key in ("tag", "channel", "importance", "timeout"):
        if value := data.get(key):
            notification_data[key] = value
    if data.get("sticky", False):
        notification_data["sticky"] = "true"
    if urgent:
        notification_data.update({"ttl": 0, "priority": "high"})
    if notification_data:
        result["data"] = notification_data
    return result


def validate_actions(value: Any) -> list[dict[str, str]]:
    """Validate friendly prompt actions and return normalized values."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_PROMPT_ACTIONS:
        raise vol.Invalid("Provide between 1 and 3 actions")
    actions: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "title"}:
            raise vol.Invalid("Each action must contain only id and title")
        action_id = str(item["id"]).strip()
        title = str(item["title"]).strip()
        if ACTION_ID_PATTERN.fullmatch(action_id) is None:
            raise vol.Invalid(
                "Action IDs must be 1-64 letters, numbers, dots, dashes, or underscores"
            )
        if action_id in seen:
            raise vol.Invalid("Action IDs must be unique")
        if not title or len(title) > MAX_ACTION_TITLE_LENGTH:
            raise vol.Invalid("Action titles must be 1-80 characters")
        seen.add(action_id)
        actions.append({"id": action_id, "title": title})
    return actions


@dataclass(frozen=True, slots=True)
class PromptSession:
    """Lookup information for one actionable notification."""

    session_id: str
    target: AndroidTarget
    tag: str | None
    actions_by_token: dict[str, str]


@dataclass(frozen=True, slots=True)
class AcknowledgementOptions:
    """Validated options for a managed notification."""

    title: str
    message: str
    tag: str
    channel: str
    acknowledgement_label: str
    repeat_interval: float
    max_attempts: int


@dataclass(slots=True)
class AcknowledgementSession:
    """Runtime state for one device/tag acknowledgement session."""

    target: AndroidTarget
    session_id: str
    action_token: str
    options: AcknowledgementOptions
    stop_event: asyncio.Event
    task: asyncio.Task[None] | None = None
    attempts_sent: int = 0

    @property
    def key(self) -> tuple[str, str]:
        """Return the replacement key for this session."""
        return (self.target.device_id, self.options.tag)


class NotificationManager:
    """Own one Companion action listener and all friendly notification state."""

    def __init__(self, hass: HomeAssistant, send: SendNotification) -> None:
        """Initialize intentionally ephemeral notification state."""
        self.hass = hass
        self._send = send
        self.prompts: OrderedDict[str, PromptSession] = OrderedDict()
        self.acknowledgements: dict[tuple[str, str], AcknowledgementSession] = {}
        self._unsubscribers: list[Callable[[], None]] = []

    def async_register(self) -> None:
        """Register shared listeners once."""
        if self._unsubscribers:
            return
        self._unsubscribers.append(
            self.hass.bus.async_listen(
                EVENT_MOBILE_APP_NOTIFICATION_ACTION,
                self._async_handle_action,
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP,
                self._async_handle_hass_stop,
            )
        )

    async def async_prompt(
        self,
        target: AndroidTarget,
        *,
        title: str,
        message: str,
        tag: str | None,
        actions: list[dict[str, str]],
    ) -> PromptSession:
        """Send one isolated actionable notification."""
        session_id = uuid4().hex
        actions_by_token = {
            f"{NOTIFICATION_ACTION_PREFIX}{uuid4().hex}": item["id"] for item in actions
        }
        session = PromptSession(session_id, target, tag, actions_by_token)
        self.prompts[session_id] = session
        while len(self.prompts) > MAX_PROMPT_SESSIONS:
            self.prompts.popitem(last=False)
        notification_data: dict[str, Any] = {
            "actions": [
                {"action": token, "title": item["title"]}
                for token, item in zip(actions_by_token, actions, strict=True)
            ]
        }
        if tag:
            notification_data["tag"] = tag
        try:
            await self._send(
                target,
                {"title": title, "message": message, "data": notification_data},
            )
        except Exception:
            self.prompts.pop(session_id, None)
            raise
        return session

    async def async_start_acknowledgement(
        self,
        target: AndroidTarget,
        options: AcknowledgementOptions,
    ) -> AcknowledgementSession:
        """Replace the same device/tag session and dispatch immediately."""
        key = (target.device_id, options.tag)
        if old := self.acknowledgements.get(key):
            await self._async_stop_acknowledgement(old, clear=True)
        session_id = uuid4().hex
        session = AcknowledgementSession(
            target=target,
            session_id=session_id,
            action_token=f"{ACK_NOTIFICATION_ACTION_PREFIX}{uuid4().hex}",
            options=options,
            stop_event=asyncio.Event(),
        )
        self.acknowledgements[key] = session
        try:
            await self._send_acknowledgement_attempt(session)
        except Exception:
            self._remove_acknowledgement_if_current(session)
            raise
        session.attempts_sent = 1
        if options.max_attempts > 1:
            session.task = self.hass.async_create_task(
                self._async_repeat_acknowledgement(session),
                f"{DOMAIN} acknowledgement {target.device_id} {options.tag}",
            )
        else:
            self._remove_acknowledgement_if_current(session)
        return session

    async def async_stop_acknowledgement(self, target: AndroidTarget, tag: str) -> bool:
        """Stop a known session and always request best-effort tag clearing."""
        session = self.acknowledgements.get((target.device_id, tag))
        if session is not None:
            await self._async_stop_acknowledgement(session, clear=True)
            return True
        await self._clear(target, tag)
        return False

    async def async_shutdown(self) -> None:
        """Cancel tasks and listeners without network cleanup."""
        sessions = list(self.acknowledgements.values())
        for session in sessions:
            session.stop_event.set()
            if session.task is not None and not session.task.done():
                session.task.cancel()
        tasks = [session.task for session in sessions if session.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.acknowledgements.clear()
        self.prompts.clear()
        while self._unsubscribers:
            self._unsubscribers.pop()()

    async def _async_repeat_acknowledgement(
        self, session: AcknowledgementSession
    ) -> None:
        try:
            while session.attempts_sent < session.options.max_attempts:
                try:
                    await asyncio.wait_for(
                        session.stop_event.wait(),
                        timeout=session.options.repeat_interval,
                    )
                except TimeoutError:
                    pass
                else:
                    break
                if session.stop_event.is_set():
                    break
                try:
                    await self._send_acknowledgement_attempt(session)
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "Managed notification attempt failed for %s",
                        session.target.device_name,
                        exc_info=True,
                    )
                session.attempts_sent += 1
        finally:
            self._remove_acknowledgement_if_current(session)

    async def _send_acknowledgement_attempt(
        self, session: AcknowledgementSession
    ) -> None:
        data = {
            "tag": session.options.tag,
            "channel": session.options.channel,
            "importance": "high",
            "ttl": 0,
            "priority": "high",
            "sticky": "true",
            "actions": [
                {
                    "action": session.action_token,
                    "title": session.options.acknowledgement_label,
                }
            ],
        }
        await self._send(
            session.target,
            {
                "title": session.options.title,
                "message": session.options.message,
                "data": data,
            },
        )

    async def _async_handle_action(self, event: Event) -> None:
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        if action.startswith(NOTIFICATION_ACTION_PREFIX):
            await self._async_handle_prompt_action(action)
            return
        if action.startswith(ACK_NOTIFICATION_ACTION_PREFIX):
            await self._async_handle_acknowledgement(action)

    async def _async_handle_prompt_action(self, token: str) -> None:
        match = next(
            (
                (session_id, session, session.actions_by_token[token])
                for session_id, session in self.prompts.items()
                if token in session.actions_by_token
            ),
            None,
        )
        if match is None:
            return
        session_id, session, action_id = match
        self.prompts.pop(session_id, None)
        self.hass.bus.async_fire(
            EVENT_NOTIFICATION_ACTION,
            {
                "device_id": session.target.device_id,
                "session_id": session.session_id,
                "action_id": action_id,
                "tag": session.tag,
            },
        )

    async def _async_handle_acknowledgement(self, token: str) -> None:
        session = next(
            (
                item
                for item in self.acknowledgements.values()
                if item.action_token == token
            ),
            None,
        )
        if session is None:
            return
        await self._async_stop_acknowledgement(session, clear=True)
        self.hass.bus.async_fire(
            EVENT_NOTIFICATION_ACKNOWLEDGED,
            {
                "device_id": session.target.device_id,
                "session_id": session.session_id,
                "tag": session.options.tag,
                "attempts": session.attempts_sent,
            },
        )

    async def _async_stop_acknowledgement(
        self, session: AcknowledgementSession, *, clear: bool
    ) -> None:
        session.stop_event.set()
        if session.task is not None and session.task is not asyncio.current_task():
            await asyncio.gather(session.task, return_exceptions=True)
        self._remove_acknowledgement_if_current(session)
        if clear:
            await self._clear(session.target, session.options.tag)

    async def _clear(self, target: AndroidTarget, tag: str) -> None:
        try:
            await self._send(target, payload("clear_notification", {"tag": tag}))
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Could not clear managed notification for %s",
                target.device_name,
                exc_info=True,
            )

    async def _async_handle_hass_stop(self, _event: Event) -> None:
        await self.async_shutdown()

    def _remove_acknowledgement_if_current(
        self, session: AcknowledgementSession
    ) -> None:
        if self.acknowledgements.get(session.key) is session:
            self.acknowledgements.pop(session.key, None)


def get_notification_manager(
    hass: HomeAssistant, send: SendNotification
) -> NotificationManager:
    """Return the integration-wide notification manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(DATA_NOTIFICATION_MANAGER)
    if manager is None:
        manager = NotificationManager(hass, send)
        manager.async_register()
        domain_data[DATA_NOTIFICATION_MANAGER] = manager
    return manager


async def async_remove_notification_manager(hass: HomeAssistant) -> None:
    """Shut down and discard all ephemeral notification state."""
    domain_data = hass.data.get(DOMAIN, {})
    manager = domain_data.pop(DATA_NOTIFICATION_MANAGER, None)
    if manager is not None:
        await manager.async_shutdown()
    if not domain_data:
        hass.data.pop(DOMAIN, None)
