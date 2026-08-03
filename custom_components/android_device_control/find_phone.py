"""Managed, ephemeral Find Phone sessions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .commands import payload, tts_payload
from .const import (
    DATA_FIND_PHONE_MANAGER,
    DOMAIN,
    EVENT_MOBILE_APP_NOTIFICATION_ACTION,
    FIND_PHONE_CONTROL_NOTIFICATION_CHANNEL,
    FIND_PHONE_EVENT_DEVICE_ID,
    FIND_PHONE_EVENT_SESSION_ID,
    FIND_PHONE_NOTIFICATION_ACTION_PREFIX,
    FIND_PHONE_NOTIFICATION_TAG,
)
from .device import AndroidTarget, resolve_android_targets

_LOGGER = logging.getLogger(__name__)

SendCommand = Callable[[AndroidTarget, dict[str, Any]], Awaitable[None]]


def _session_id_from_action(action: object) -> str | None:
    """Extract a non-empty session ID from a Find Phone action token."""
    if not isinstance(action, str) or not action.startswith(
        FIND_PHONE_NOTIFICATION_ACTION_PREFIX
    ):
        return None
    session_id = action.removeprefix(FIND_PHONE_NOTIFICATION_ACTION_PREFIX)
    if not session_id or session_id.isspace():
        return None
    return session_id


@dataclass(frozen=True, slots=True)
class FindPhoneOptions:
    """Validated options for one Find Phone request."""

    wake_screen: bool
    flashlight: bool
    sound_mode: str
    message: str
    repeat: bool
    max_attempts: int
    repeat_interval: float
    show_stop_action: bool

    @property
    def attempts(self) -> int:
        """Return the effective number of audible attempts."""
        return self.max_attempts if self.repeat else 1


@dataclass(slots=True)
class FindPhoneSession:
    """Runtime state for one device's Find Phone session."""

    device_id: str
    target: AndroidTarget
    session_id: str
    stop_event: asyncio.Event
    options: FindPhoneOptions
    task: asyncio.Task[None] | None = None
    attempts_sent: int = 0

    @property
    def flashlight_enabled(self) -> bool:
        """Return whether this session requested flashlight-on."""
        return self.options.flashlight


class FindPhoneManager:
    """Own Find Phone sessions and Companion notification listeners."""

    def __init__(self, hass: HomeAssistant, send_command: SendCommand) -> None:
        """Initialize an empty, intentionally non-persistent manager."""
        self.hass = hass
        self._send_command = send_command
        self.sessions: dict[str, FindPhoneSession] = {}
        self._unsubscribers: list[Callable[[], None]] = []

    def async_register(self) -> None:
        """Register event listeners once."""
        if self._unsubscribers:
            return
        self._unsubscribers.append(
            self.hass.bus.async_listen(
                EVENT_MOBILE_APP_NOTIFICATION_ACTION,
                self._async_handle_notification_action,
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP,
                self._async_handle_hass_stop,
            )
        )

    async def async_start(
        self, target: AndroidTarget, options: FindPhoneOptions
    ) -> FindPhoneSession:
        """Replace any existing device session and send its first attempt now."""
        old_session = self.sessions.get(target.device_id)
        if old_session is not None:
            await self._async_stop_session(old_session, cleanup=True)

        session = FindPhoneSession(
            device_id=target.device_id,
            target=target,
            session_id=uuid4().hex,
            stop_event=asyncio.Event(),
            options=options,
        )
        self.sessions[target.device_id] = session

        await self._async_send_attempt(session, first=True)
        session.attempts_sent = 1

        if session.stop_event.is_set() or options.attempts == 1:
            self._remove_if_current(session)
            return session

        session.task = self.hass.async_create_task(
            self._async_repeat(session),
            f"{DOMAIN} Find Phone {target.device_id}",
        )
        return session

    async def async_stop(
        self, target: AndroidTarget, *, turn_off_flashlight: bool = False
    ) -> None:
        """Stop one session and perform best-effort device cleanup."""
        session = self.sessions.get(target.device_id)
        if session is not None:
            await self._async_stop_session(session, cleanup=True)
            return
        await self._async_cleanup(target, turn_off_flashlight=turn_off_flashlight)

    async def async_shutdown(self) -> None:
        """Cancel all ephemeral work without sending phone-side commands."""
        sessions = list(self.sessions.values())
        for session in sessions:
            session.stop_event.set()
            if session.task is not None and not session.task.done():
                session.task.cancel()
        tasks = [session.task for session in sessions if session.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.sessions.clear()
        while self._unsubscribers:
            self._unsubscribers.pop()()

    async def _async_repeat(self, session: FindPhoneSession) -> None:
        """Send later attempts until stopped or bounded attempts are exhausted."""
        try:
            while session.attempts_sent < session.options.attempts:
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
                await self._async_send_attempt(session, first=False)
                session.attempts_sent += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Unexpected Find Phone session failure for Android device %s",
                session.device_id,
            )
        finally:
            self._remove_if_current(session)

    async def _async_send_attempt(
        self, session: FindPhoneSession, *, first: bool
    ) -> None:
        """Send one attempt, isolating transient command failures."""
        commands: list[dict[str, Any]] = []
        if first and session.options.wake_screen:
            commands.append(payload("command_screen_on", {"command": "reset"}))
        if first and session.options.flashlight:
            commands.append(payload("command_flashlight", {"command": "turn_on"}))
        if (
            first
            and session.options.sound_mode == "tts"
            and session.options.show_stop_action
        ):
            commands.append(self._control_notification(session))
        commands.append(self._sound_notification(session))

        for command in commands:
            if session.stop_event.is_set():
                return
            try:
                await self._send_command(session.target, command)
            except Exception:  # noqa: BLE001 - background work must not leak failures
                _LOGGER.warning(
                    "Find Phone command %s failed for %s; later attempts will continue",
                    command["message"],
                    session.target.device_name,
                    exc_info=True,
                )

    def _sound_notification(self, session: FindPhoneSession) -> dict[str, Any]:
        """Build the audible payload for a session."""
        if session.options.sound_mode == "tts":
            return tts_payload(session.options.message, "alarm_max")

        data: dict[str, Any] = {
            "ttl": 0,
            "priority": "high",
            "channel": "alarm_stream",
            "tag": FIND_PHONE_NOTIFICATION_TAG,
        }
        if session.options.show_stop_action:
            data.update(self._action_data(session))
        return {
            "title": "Find Phone",
            "message": "Finding phone",
            "data": data,
        }

    def _control_notification(self, session: FindPhoneSession) -> dict[str, Any]:
        """Build a quiet actionable notification for TTS mode."""
        data: dict[str, Any] = {
            "tag": FIND_PHONE_NOTIFICATION_TAG,
            "channel": FIND_PHONE_CONTROL_NOTIFICATION_CHANNEL,
            "importance": "low",
        }
        data.update(self._action_data(session))
        return {
            "title": "Find Phone",
            "message": "Finding phone",
            "data": data,
        }

    @staticmethod
    def _action_data(session: FindPhoneSession) -> dict[str, Any]:
        """Return restart-safe Companion actionable-notification metadata."""
        action = f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}{session.session_id}"
        return {
            "actions": [{"action": action, "title": "Stop ringing"}],
            FIND_PHONE_EVENT_DEVICE_ID: session.device_id,
            FIND_PHONE_EVENT_SESSION_ID: session.session_id,
        }

    async def _async_stop_session(
        self, session: FindPhoneSession, *, cleanup: bool
    ) -> None:
        """Interrupt one session and optionally clean up its phone state."""
        session.stop_event.set()
        if session.task is not None and session.task is not asyncio.current_task():
            await asyncio.gather(session.task, return_exceptions=True)
        self._remove_if_current(session)
        if cleanup:
            await self._async_cleanup(
                session.target,
                turn_off_flashlight=session.flashlight_enabled,
            )

    async def _async_cleanup(
        self, target: AndroidTarget, *, turn_off_flashlight: bool
    ) -> None:
        """Send independent, best-effort Companion cleanup commands."""
        commands = [
            payload("command_stop_tts"),
            payload("clear_notification", {"tag": FIND_PHONE_NOTIFICATION_TAG}),
        ]
        if turn_off_flashlight:
            commands.append(payload("command_flashlight", {"command": "turn_off"}))
        for command in commands:
            try:
                await self._send_command(target, command)
            except Exception:  # noqa: BLE001 - each cleanup command is best effort
                _LOGGER.warning(
                    "Find Phone cleanup command %s failed for %s",
                    command["message"],
                    target.device_name,
                    exc_info=True,
                )

    async def _async_handle_notification_action(self, event: Event) -> None:
        """Stop an active session or perform conservative restart cleanup."""
        action = event.data.get("action")
        if not isinstance(action, str) or not action.startswith(
            FIND_PHONE_NOTIFICATION_ACTION_PREFIX
        ):
            return

        session_id = _session_id_from_action(action)
        if session_id is None:
            _LOGGER.warning("Ignoring malformed Find Phone stop action")
            return

        matching_session = next(
            (
                session
                for session in self.sessions.values()
                if session.session_id == session_id
            ),
            None,
        )
        if matching_session is not None:
            await self._async_stop_session(matching_session, cleanup=True)
            return

        device_id = event.data.get(FIND_PHONE_EVENT_DEVICE_ID)
        metadata_session_id = event.data.get(FIND_PHONE_EVENT_SESSION_ID)
        restart_device_id = (
            device_id
            if isinstance(device_id, str)
            and bool(device_id)
            and isinstance(metadata_session_id, str)
            and metadata_session_id == session_id
            else None
        )
        current = (
            self.sessions.get(restart_device_id)
            if restart_device_id is not None
            else None
        )
        if current is not None and current.session_id != session_id:
            _LOGGER.warning(
                "Ignoring stale Find Phone notification action for Android device %s",
                device_id,
            )
            return

        active_sessions = list(self.sessions.values())
        if len(active_sessions) == 1:
            _LOGGER.warning(
                "Find Phone stop action for session %s did not match a current "
                "session; stopping the sole active session as a fallback",
                session_id,
            )
            await self._async_stop_session(active_sessions[0], cleanup=True)
            return

        await self._async_restart_cleanup(event, restart_device_id)

    async def _async_restart_cleanup(self, event: Event, device_id: str | None) -> None:
        """Use trustworthy returned metadata for best-effort post-restart cleanup."""
        if device_id is None or event.data.get("tag") != FIND_PHONE_NOTIFICATION_TAG:
            _LOGGER.warning(
                "Ignoring Find Phone stop action because metadata is insufficient "
                "for restart-safe cleanup"
            )
            return

        try:
            targets = resolve_android_targets(self.hass, [device_id])
        except HomeAssistantError:
            _LOGGER.warning(
                "Could not resolve Android device %s from a Find Phone notification "
                "action",
                device_id,
                exc_info=True,
            )
            return
        if len(targets) != 1:
            _LOGGER.warning(
                "Ignoring ambiguous Find Phone notification action for device %s",
                device_id,
            )
            return
        await self._async_cleanup(targets[0], turn_off_flashlight=False)

    async def _async_handle_hass_stop(self, _event: Event) -> None:
        """Abandon all sessions when Home Assistant stops."""
        await self.async_shutdown()

    def _remove_if_current(self, session: FindPhoneSession) -> None:
        """Remove a session without allowing an old task to remove its replacement."""
        if self.sessions.get(session.device_id) is session:
            self.sessions.pop(session.device_id, None)


def get_find_phone_manager(
    hass: HomeAssistant, send_command: SendCommand
) -> FindPhoneManager:
    """Return the integration's single Find Phone manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(DATA_FIND_PHONE_MANAGER)
    if manager is None:
        manager = FindPhoneManager(hass, send_command)
        manager.async_register()
        domain_data[DATA_FIND_PHONE_MANAGER] = manager
    return manager


async def async_remove_find_phone_manager(hass: HomeAssistant) -> None:
    """Shut down and discard the ephemeral manager."""
    domain_data = hass.data.get(DOMAIN, {})
    manager = domain_data.pop(DATA_FIND_PHONE_MANAGER, None)
    if manager is not None:
        await manager.async_shutdown()
    if not domain_data:
        hass.data.pop(DOMAIN, None)
