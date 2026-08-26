"""Friendly notifications and bounded acknowledgement sessions."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.storage import Store

from .commands import payload
from .const import (
    ACK_NOTIFICATION_ACTION_PREFIX,
    DATA_NOTIFICATION_MANAGER,
    DOMAIN,
    EVENT_MOBILE_APP_NOTIFICATION_ACTION,
    EVENT_MOBILE_APP_NOTIFICATION_RECEIVED,
    EVENT_NOTIFICATION_ACKNOWLEDGED,
    EVENT_NOTIFICATION_ACTION,
    EVENT_NOTIFICATION_RECEIVED,
    NOTIFICATION_ACTION_PREFIX,
    NOTIFICATION_CONFIRMATION_KEY,
)
from .device import AndroidTarget

_LOGGER = logging.getLogger(__name__)

SendNotification = Callable[[AndroidTarget, dict[str, Any]], Awaitable[None]]
ACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_PROMPT_ACTIONS = 3
MAX_PROMPT_SESSIONS = 100
MAX_CONFIRMATION_SESSIONS = 100
MAX_PERSISTED_ACKNOWLEDGEMENTS = 100
MAX_ACTION_TITLE_LENGTH = 80
STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}.notification_sessions"
SESSION_TTL = 24 * 60 * 60


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
    if data.get("show_in_android_auto", False):
        notification_data["car_ui"] = True
    if data.get("confirm_delivery", False):
        notification_data["confirmation"] = True
    if urgent:
        notification_data.update({"ttl": 0, "priority": "high"})
    if notification_data:
        result["data"] = notification_data
    return result


def progress_notification_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build an official Companion progress notification."""
    result = notification_payload(data)
    notification_data = result.setdefault("data", {})
    if data["indeterminate"]:
        notification_data["progress_indeterminate"] = True
    else:
        current, maximum = data.get("current"), data.get("maximum")
        if current is None or maximum is None:
            raise vol.Invalid(
                "Current and maximum are required for determinate progress"
            )
        if maximum <= 0 or current < 0 or current > maximum:
            raise vol.Invalid(
                "Progress must satisfy 0 <= current <= maximum and maximum > 0"
            )
        notification_data.update({"progress": current, "progress_max": maximum})
    return result


def image_notification_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build an official Companion image attachment notification."""
    result = notification_payload(data)
    result.setdefault("data", {})["image"] = data["image"]
    return result


def live_update_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build the isolated Android 16 Companion Live Update payload."""
    result = notification_payload(data)
    notification_data = result.setdefault("data", {})
    notification_data["live_update"] = True
    for source, target in (
        ("critical_text", "critical_text"),
        ("icon", "notification_icon"),
    ):
        if value := data.get(source):
            notification_data[target] = value
    current, maximum = data.get("current"), data.get("maximum")
    if (current is None) != (maximum is None):
        raise vol.Invalid("Current and maximum must be provided together")
    if current is not None and (maximum <= 0 or current < 0 or current > maximum):
        raise vol.Invalid(
            "Progress must satisfy 0 <= current <= maximum and maximum > 0"
        )
    if current is not None:
        notification_data.update({"progress": current, "progress_max": maximum})
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
    created_at: float


@dataclass(frozen=True, slots=True)
class ConfirmationSession:
    """Minimal correlation metadata for one Companion receipt confirmation."""

    session_id: str
    target: AndroidTarget
    tag: str | None
    created_at: float


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
    created_at: float = 0

    @property
    def key(self) -> tuple[str, str]:
        """Return the replacement key for this session."""
        return (self.target.device_id, self.options.tag)


@dataclass(frozen=True, slots=True)
class StaleAcknowledgement:
    """Minimal restart metadata for a notification that is being reconciled."""

    target: AndroidTarget
    session_id: str
    action_token: str
    tag: str
    attempts_sent: int
    created_at: float


class NotificationManager:
    """Own one Companion action listener and all friendly notification state."""

    def __init__(
        self,
        hass: HomeAssistant,
        send: SendNotification,
        store: Store[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize bounded runtime and persisted action state."""
        self.hass = hass
        self._send = send
        self._store = (
            store
            if store is not None
            else (
                Store(hass, STORE_VERSION, STORE_KEY)
                if hasattr(hass, "config")
                else None
            )
        )
        self._loaded = self._store is None
        self._load_lock = asyncio.Lock()
        self.prompts: OrderedDict[str, PromptSession] = OrderedDict()
        self.confirmations: OrderedDict[str, ConfirmationSession] = OrderedDict()
        self.acknowledgements: dict[tuple[str, str], AcknowledgementSession] = {}
        self.stale_acknowledgements: dict[str, StaleAcknowledgement] = {}
        self._ack_start_lock = asyncio.Lock()
        self._unsubscribers: list[Callable[[], None]] = []
        self._restore_task: asyncio.Task[None] | None = None

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
            self.hass.bus.async_listen(
                EVENT_MOBILE_APP_NOTIFICATION_RECEIVED,
                self._async_handle_received,
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP,
                self._async_handle_hass_stop,
            )
        )
        if self._store is not None:
            self._restore_task = self.hass.async_create_task(
                self._async_ensure_loaded(),
                f"{DOMAIN} restore notification sessions",
            )

    async def _async_ensure_loaded(self) -> None:
        """Restore bounded metadata once and reconcile managed notifications."""
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            raw: dict[str, Any] | None = None
            try:
                raw = (
                    await self._store.async_load() if self._store is not None else None
                )
            except Exception:  # noqa: BLE001 - storage must not block startup
                _LOGGER.warning(
                    "Could not load notification session metadata", exc_info=True
                )
            self._loaded = True
            if isinstance(raw, dict):
                self._restore(raw)
            await self._async_reconcile_stale_acknowledgements()
            await self._async_save()

    def _restore(self, raw: dict[str, Any]) -> None:
        """Validate and restore only minimal, unexpired session metadata."""
        cutoff = time.time() - SESSION_TTL
        prompt_items = raw.get("prompts", [])
        for item in prompt_items if isinstance(prompt_items, list) else []:
            try:
                session = PromptSession(
                    session_id=item["session_id"],
                    target=_target_from_dict(item["target"]),
                    tag=item.get("tag"),
                    actions_by_token=dict(item["actions_by_token"]),
                    created_at=float(item["created_at"]),
                )
                if (
                    session.created_at >= cutoff
                    and session.session_id
                    and session.actions_by_token
                    and all(
                        isinstance(token, str)
                        and token.startswith(NOTIFICATION_ACTION_PREFIX)
                        and isinstance(action_id, str)
                        for token, action_id in session.actions_by_token.items()
                    )
                ):
                    self.prompts[session.session_id] = session
            except (KeyError, TypeError, ValueError):
                continue
        while len(self.prompts) > MAX_PROMPT_SESSIONS:
            self.prompts.popitem(last=False)

        self._restore_confirmations(raw, cutoff)

        ack_items = raw.get("acknowledgements", [])
        for item in ack_items if isinstance(ack_items, list) else []:
            try:
                stale = StaleAcknowledgement(
                    target=_target_from_dict(item["target"]),
                    session_id=item["session_id"],
                    action_token=item["action_token"],
                    tag=item["tag"],
                    attempts_sent=int(item["attempts_sent"]),
                    created_at=float(item["created_at"]),
                )
                if (
                    stale.created_at >= cutoff
                    and stale.session_id
                    and stale.tag
                    and stale.action_token.startswith(ACK_NOTIFICATION_ACTION_PREFIX)
                ):
                    self.stale_acknowledgements[stale.action_token] = stale
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        while len(self.stale_acknowledgements) > MAX_PERSISTED_ACKNOWLEDGEMENTS:
            self.stale_acknowledgements.pop(next(iter(self.stale_acknowledgements)))

    def _restore_confirmations(self, raw: dict[str, Any], cutoff: float) -> None:
        """Restore only valid, unexpired canonical target correlations."""
        confirmation_items = raw.get("confirmations", [])
        for item in confirmation_items if isinstance(confirmation_items, list) else []:
            try:
                session = ConfirmationSession(
                    session_id=item["session_id"],
                    target=_target_from_dict(item["target"]),
                    tag=item.get("tag"),
                    created_at=float(item["created_at"]),
                )
                if (
                    session.created_at >= cutoff
                    and isinstance(session.session_id, str)
                    and session.session_id
                    and (session.tag is None or isinstance(session.tag, str))
                ):
                    self.confirmations[session.session_id] = session
            except (KeyError, TypeError, ValueError):
                continue
        while len(self.confirmations) > MAX_CONFIRMATION_SESSIONS:
            self.confirmations.popitem(last=False)

    async def _async_reconcile_stale_acknowledgements(self) -> None:
        """Best-effort clear notifications whose repeating tasks ended at restart."""
        await asyncio.gather(
            *(
                self._clear(stale.target, stale.tag)
                for stale in self.stale_acknowledgements.values()
            )
        )

    async def _async_save(self) -> None:
        """Persist the bounded metadata needed to recognize existing actions."""
        if self._store is None or not self._loaded:
            return
        acknowledgements = (
            [_ack_to_dict(session) for session in self.acknowledgements.values()]
            + [
                _stale_ack_to_dict(stale)
                for stale in self.stale_acknowledgements.values()
            ]
        )[-MAX_PERSISTED_ACKNOWLEDGEMENTS:]
        try:
            await self._store.async_save(
                {
                    "prompts": [
                        _prompt_to_dict(item) for item in self.prompts.values()
                    ],
                    "confirmations": [
                        _confirmation_to_dict(item)
                        for item in self.confirmations.values()
                    ],
                    "acknowledgements": acknowledgements,
                }
            )
        except Exception:  # noqa: BLE001 - dispatch already succeeded
            _LOGGER.warning(
                "Could not save notification session metadata", exc_info=True
            )

    async def async_prompt(  # noqa: PLR0913 - explicit public prompt options
        self,
        target: AndroidTarget,
        *,
        title: str,
        message: str,
        tag: str | None,
        actions: list[dict[str, str]],
        require_unlock: bool = False,
        show_in_android_auto: bool = False,
        confirm_delivery: bool = False,
        text_input: bool = False,
    ) -> PromptSession:
        """Send one isolated actionable notification."""
        await self._async_ensure_loaded()
        session_id = uuid4().hex
        actions_by_token = {
            f"{NOTIFICATION_ACTION_PREFIX}{uuid4().hex}": item["id"] for item in actions
        }
        session = PromptSession(session_id, target, tag, actions_by_token, time.time())
        self.prompts[session_id] = session
        while len(self.prompts) > MAX_PROMPT_SESSIONS:
            self.prompts.popitem(last=False)
        action_payloads = []
        for token, item in zip(actions_by_token, actions, strict=True):
            action_data: dict[str, Any] = {"action": token, "title": item["title"]}
            if require_unlock:
                action_data["authenticationRequired"] = True
            if text_input:
                action_data["behavior"] = "textInput"
            action_payloads.append(action_data)
        notification_data: dict[str, Any] = {"actions": action_payloads}
        if tag:
            notification_data["tag"] = tag
        if show_in_android_auto:
            notification_data["car_ui"] = True
        if confirm_delivery:
            notification_data["confirmation"] = True
            notification_data[NOTIFICATION_CONFIRMATION_KEY] = session_id
            self._add_confirmation(session_id, target, tag)
            await self._async_save()
        try:
            await self._send(
                target,
                {"title": title, "message": message, "data": notification_data},
            )
        except Exception:
            self.prompts.pop(session_id, None)
            self.confirmations.pop(session_id, None)
            await self._async_save()
            raise
        await self._async_save()
        return session

    async def async_send_confirmed(
        self,
        target: AndroidTarget,
        notify_payload: dict[str, Any],
        tag: str | None,
    ) -> ConfirmationSession:
        """Send with unique, persisted correlation for one canonical target."""
        await self._async_ensure_loaded()
        session_id = uuid4().hex
        session = self._add_confirmation(session_id, target, tag)
        correlated_payload = dict(notify_payload)
        notification_data = dict(correlated_payload.get("data", {}))
        notification_data.update(
            {
                "confirmation": True,
                NOTIFICATION_CONFIRMATION_KEY: session_id,
            }
        )
        correlated_payload["data"] = notification_data
        await self._async_save()
        try:
            await self._send(target, correlated_payload)
        except Exception:
            self.confirmations.pop(session_id, None)
            await self._async_save()
            raise
        return session

    def _add_confirmation(
        self, session_id: str, target: AndroidTarget, tag: str | None
    ) -> ConfirmationSession:
        """Add one bounded canonical-target correlation."""
        session = ConfirmationSession(session_id, target, tag, time.time())
        self.confirmations[session_id] = session
        while len(self.confirmations) > MAX_CONFIRMATION_SESSIONS:
            self.confirmations.popitem(last=False)
        return session

    async def async_start_acknowledgement(
        self,
        target: AndroidTarget,
        options: AcknowledgementOptions,
    ) -> AcknowledgementSession:
        """Dispatch a replacement before retiring the prior working session."""
        await self._async_ensure_loaded()
        key = (target.device_id, options.tag)
        old: AcknowledgementSession | None = None
        async with self._ack_start_lock:
            old = self.acknowledgements.get(key)
            session = AcknowledgementSession(
                target=target,
                session_id=uuid4().hex,
                action_token=f"{ACK_NOTIFICATION_ACTION_PREFIX}{uuid4().hex}",
                options=options,
                stop_event=asyncio.Event(),
                created_at=time.time(),
            )
            # Sending to the same Companion tag atomically replaces the phone-side
            # notification. Do not clear the tag while retiring the old task.
            await self._send_acknowledgement_attempt(session)
            session.attempts_sent = 1
            if old is not None:
                # A repeat waiting for this lock will observe the stop/current checks
                # below and cannot overwrite the replacement after ownership moves.
                old.stop_event.set()
            self.acknowledgements[key] = session
            session.task = self.hass.async_create_task(
                self._async_repeat_acknowledgement(session),
                f"{DOMAIN} acknowledgement {target.device_id} {options.tag}",
            )
            await self._async_save()
        if (
            old is not None
            and old.task is not None
            and old.task is not asyncio.current_task()
        ):
            # Do not wait while holding _ack_start_lock: the old repeat may be
            # queued on that lock so it can verify that it no longer owns the tag.
            await asyncio.gather(old.task, return_exceptions=True)
        return session

    async def async_stop_acknowledgement(self, target: AndroidTarget, tag: str) -> bool:
        """Stop a known session and always request best-effort tag clearing."""
        await self._async_ensure_loaded()
        session = self.acknowledgements.get((target.device_id, tag))
        if session is not None:
            await self._async_stop_acknowledgement(session, clear=True)
            return True
        self.stale_acknowledgements = {
            token: stale
            for token, stale in self.stale_acknowledgements.items()
            if (stale.target.device_id, stale.tag) != (target.device_id, tag)
        }
        await self._clear(target, tag)
        await self._async_save()
        return False

    async def async_shutdown(self) -> None:
        """Cancel tasks and listeners without network cleanup."""
        if (
            self._restore_task is not None
            and self._restore_task is not asyncio.current_task()
            and not self._restore_task.done()
        ):
            await asyncio.gather(self._restore_task, return_exceptions=True)
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
        self.confirmations.clear()
        self.stale_acknowledgements.clear()
        while self._unsubscribers:
            self._unsubscribers.pop()()

    async def _async_repeat_acknowledgement(
        self, session: AcknowledgementSession
    ) -> None:
        cleanup = False
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
                async with self._ack_start_lock:
                    if self.acknowledgements.get(session.key) is not session:
                        return
                    if session.stop_event.is_set():
                        return
                    try:
                        await self._send_acknowledgement_attempt(session)
                    except Exception:  # noqa: BLE001 - later attempts remain useful
                        _LOGGER.warning(
                            "Managed notification attempt failed for %s",
                            session.target.device_name,
                            exc_info=True,
                        )
                session.attempts_sent += 1
                await self._async_save()
            if not session.stop_event.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        session.stop_event.wait(),
                        timeout=session.options.repeat_interval,
                    )
            cleanup = not session.stop_event.is_set()
        finally:
            if cleanup and not session.stop_event.is_set():
                await self._clear(session.target, session.options.tag)
                self._remove_acknowledgement_if_current(session)
                await self._async_save()

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
        await self._async_ensure_loaded()
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        if action.startswith(NOTIFICATION_ACTION_PREFIX):
            await self._async_handle_prompt_action(action, event)
            return
        if action.startswith(ACK_NOTIFICATION_ACTION_PREFIX):
            await self._async_handle_acknowledgement(action)

    async def _async_handle_prompt_action(self, token: str, event: Event) -> None:
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
        await self._async_save()
        event_data = {
            "device_id": session.target.device_id,
            "session_id": session.session_id,
            "action_id": action_id,
            "tag": session.tag,
        }
        if isinstance(event.data.get("reply_text"), str):
            event_data["response_text"] = event.data["reply_text"]
        self.hass.bus.async_fire(
            EVENT_NOTIFICATION_ACTION,
            event_data,
        )

    async def _async_handle_received(self, event: Event) -> None:
        """Translate only confirmations requested by this integration."""
        await self._async_ensure_loaded()
        session_id = event.data.get(NOTIFICATION_CONFIRMATION_KEY)
        if not isinstance(session_id, str) or not session_id:
            return
        session = self.confirmations.pop(session_id, None)
        if session is None:
            return
        await self._async_save()
        if session.created_at < time.time() - SESSION_TTL:
            return
        self.hass.bus.async_fire(
            EVENT_NOTIFICATION_RECEIVED,
            {
                "device_id": session.target.device_id,
                "tag": session.tag,
                "session_id": session.session_id,
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
            stale = self.stale_acknowledgements.pop(token, None)
            if stale is None:
                return
            await self._async_save()
            self.hass.bus.async_fire(
                EVENT_NOTIFICATION_ACKNOWLEDGED,
                {
                    "device_id": stale.target.device_id,
                    "session_id": stale.session_id,
                    "tag": stale.tag,
                    "attempts": stale.attempts_sent,
                    "reconciled_after_restart": True,
                },
            )
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
        self,
        session: AcknowledgementSession,
        *,
        clear: bool,
        save: bool = True,
    ) -> None:
        session.stop_event.set()
        if session.task is not None and session.task is not asyncio.current_task():
            await asyncio.gather(session.task, return_exceptions=True)
        self._remove_acknowledgement_if_current(session)
        if clear:
            await self._clear(session.target, session.options.tag)
        if save:
            await self._async_save()

    async def _clear(self, target: AndroidTarget, tag: str) -> None:
        try:
            await self._send(target, payload("clear_notification", {"tag": tag}))
        except Exception:  # noqa: BLE001 - best-effort reconciliation
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


def _target_to_dict(target: AndroidTarget) -> dict[str, str]:
    """Serialize the stable routing fields needed for restart reconciliation."""
    return {
        "device_id": target.device_id,
        "device_name": target.device_name,
        "webhook_id": target.webhook_id,
        "notify_service": target.notify_service,
    }


def _target_from_dict(data: dict[str, Any]) -> AndroidTarget:
    """Validate a persisted Android target."""
    values = {
        key: data[key]
        for key in ("device_id", "device_name", "webhook_id", "notify_service")
    }
    if not all(isinstance(value, str) and value for value in values.values()):
        raise ValueError("Invalid persisted Android target")
    return AndroidTarget(**values)


def _prompt_to_dict(session: PromptSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "target": _target_to_dict(session.target),
        "tag": session.tag,
        "actions_by_token": session.actions_by_token,
        "created_at": session.created_at,
    }


def _confirmation_to_dict(session: ConfirmationSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "target": _target_to_dict(session.target),
        "tag": session.tag,
        "created_at": session.created_at,
    }


def _ack_to_dict(session: AcknowledgementSession) -> dict[str, Any]:
    return {
        "target": _target_to_dict(session.target),
        "session_id": session.session_id,
        "action_token": session.action_token,
        "tag": session.options.tag,
        "attempts_sent": session.attempts_sent,
        "created_at": session.created_at,
    }


def _stale_ack_to_dict(stale: StaleAcknowledgement) -> dict[str, Any]:
    return {
        "target": _target_to_dict(stale.target),
        "session_id": stale.session_id,
        "action_token": stale.action_token,
        "tag": stale.tag,
        "attempts_sent": stale.attempts_sent,
        "created_at": stale.created_at,
    }


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
