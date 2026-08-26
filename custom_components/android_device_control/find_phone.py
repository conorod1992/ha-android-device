"""Managed, ephemeral Find Phone sessions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from uuid import uuid4

from homeassistant.const import (
    ATTR_DEVICE_ID,
    EVENT_HOMEASSISTANT_STOP,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

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
    LEGACY_FIND_PHONE_NOTIFICATION_TAG,
)
from .device import (
    DATA_CONFIG_ENTRIES,
    MOBILE_APP_DOMAIN,
    AndroidTarget,
    resolve_android_targets,
)

_LOGGER = logging.getLogger(__name__)

SendCommand = Callable[[AndroidTarget, dict[str, Any]], Awaitable[None]]

EVENT_ANDROID_INTENT_RECEIVED = "android.intent_received"
USER_PRESENT_INTENT = "android.intent.action.USER_PRESENT"
KEYGUARD_LOCKED_UNIQUE_ID = "keyguard_locked"


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
    stop_when_unlocked: bool = True

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
    keyguard_unsubscribe: Callable[[], None] | None = None
    stop_complete: asyncio.Event = field(default_factory=asyncio.Event)


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
            self.hass.bus.async_listen(
                EVENT_ANDROID_INTENT_RECEIVED,
                self._async_handle_android_intent,
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
        if options.stop_when_unlocked:
            self._subscribe_keyguard(session)

        dispatched = await self._async_send_attempt(session, first=True)
        if dispatched == 0:
            self._unsubscribe_keyguard(session)
            self._remove_if_current(session)
            session.stop_complete.set()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="dispatch_failed",
                translation_placeholders={"devices": target.device_name},
            )
        session.attempts_sent = 1

        if session.stop_event.is_set():
            await session.stop_complete.wait()
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
        await self._async_cleanup(
            target,
            clear_legacy=True,
            turn_off_flashlight=turn_off_flashlight,
        )

    async def async_shutdown(self) -> None:
        """Cancel all ephemeral work without sending phone-side commands."""
        sessions = list(self.sessions.values())
        for session in sessions:
            self._unsubscribe_keyguard(session)
            session.stop_event.set()
            if session.task is not None and not session.task.done():
                session.task.cancel()
        tasks = [session.task for session in sessions if session.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.sessions.clear()
        for session in sessions:
            session.stop_complete.set()
        while self._unsubscribers:
            self._unsubscribers.pop()()

    async def _async_repeat(self, session: FindPhoneSession) -> None:
        """Send later attempts until stopped or bounded attempts are exhausted."""
        cleanup = False
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

            if not session.stop_event.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        session.stop_event.wait(),
                        timeout=session.options.repeat_interval,
                    )
            cleanup = not session.stop_event.is_set()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Unexpected Find Phone session failure for Android device %s",
                session.device_id,
            )
            cleanup = True
        finally:
            if cleanup and not session.stop_event.is_set():
                await self._async_stop_session(session, cleanup=True)

    async def _async_send_attempt(
        self, session: FindPhoneSession, *, first: bool
    ) -> int:
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

        dispatched = 0
        for command in commands:
            if session.stop_event.is_set():
                return dispatched
            try:
                await self._send_command(session.target, command)
                dispatched += 1
            except Exception:  # noqa: BLE001 - isolate each background dispatch
                _LOGGER.warning(
                    "Find Phone command %s failed for %s; later attempts will continue",
                    command["message"],
                    session.target.device_name,
                    exc_info=True,
                )
        return dispatched

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
        if self.sessions.get(session.device_id) is not session:
            return
        if session.stop_event.is_set():
            await session.stop_complete.wait()
            return

        session.stop_event.set()
        self._unsubscribe_keyguard(session)
        if session.task is not None and session.task is not asyncio.current_task():
            await asyncio.gather(session.task, return_exceptions=True)
        try:
            if cleanup:
                await self._async_cleanup(
                    session.target,
                    clear_legacy=False,
                    stop_tts=session.options.sound_mode == "tts",
                    turn_off_flashlight=False,
                )
        finally:
            self._remove_if_current(session)
            session.stop_complete.set()

    def _subscribe_keyguard(self, session: FindPhoneSession) -> None:
        """Watch the enabled Companion keyguard sensor for one active session."""
        if not hasattr(self.hass, "states"):
            return
        entity_id = _keyguard_entity_id(self.hass, session.target)
        if entity_id is None:
            return
        session.keyguard_unsubscribe = async_track_state_change_event(
            self.hass,
            [entity_id],
            partial(self._async_handle_keyguard_state, session),
        )

    @staticmethod
    def _unsubscribe_keyguard(session: FindPhoneSession) -> None:
        """Remove a session's state listener at most once."""
        if session.keyguard_unsubscribe is None:
            return
        session.keyguard_unsubscribe()
        session.keyguard_unsubscribe = None

    async def _async_handle_keyguard_state(
        self, session: FindPhoneSession, event: Event
    ) -> None:
        """Stop only for a locked-to-unlocked transition from this session's sensor."""
        if not session.options.stop_when_unlocked:
            return
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if (
            old_state is None
            or new_state is None
            or old_state.state != STATE_ON
            or new_state.state != STATE_OFF
        ):
            return
        await self._async_stop_session(session, cleanup=True)

    async def _async_handle_android_intent(self, event: Event) -> None:
        """Stop the session whose Mobile App registration reported USER_PRESENT."""
        if event.data.get("intent") != USER_PRESENT_INTENT:
            return
        registration_device_id = event.data.get(ATTR_DEVICE_ID)
        if not isinstance(registration_device_id, str) or not registration_device_id:
            return

        matches = [
            session
            for session in list(self.sessions.values())
            if session.options.stop_when_unlocked
            and _registration_device_id(self.hass, session.target)
            == registration_device_id
        ]
        if len(matches) != 1:
            return
        await self._async_stop_session(matches[0], cleanup=True)

    async def _async_cleanup(
        self,
        target: AndroidTarget,
        *,
        clear_legacy: bool,
        stop_tts: bool = False,
        turn_off_flashlight: bool,
    ) -> None:
        """Send independent, best-effort Companion cleanup commands."""
        commands = [payload("clear_notification", {"tag": FIND_PHONE_NOTIFICATION_TAG})]
        if clear_legacy:
            commands.append(
                payload(
                    "clear_notification",
                    {"tag": LEGACY_FIND_PHONE_NOTIFICATION_TAG},
                )
            )
        if stop_tts:
            commands.insert(0, payload("command_stop_tts"))
        if turn_off_flashlight:
            commands.append(payload("command_flashlight", {"command": "turn_off"}))
        for command in commands:
            try:
                await self._send_command(target, command)
            except Exception:  # noqa: BLE001 - cleanup commands are independent
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
        if device_id is None or event.data.get("tag") not in {
            FIND_PHONE_NOTIFICATION_TAG,
            LEGACY_FIND_PHONE_NOTIFICATION_TAG,
        }:
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
        # After restart the sound mode is unknown, so stopping TTS could interrupt
        # unrelated Companion App speech. Clear only integration-owned notification
        # state; explicit stop_tts remains available to the user.
        await self._async_cleanup(
            targets[0],
            clear_legacy=True,
            stop_tts=False,
            turn_off_flashlight=False,
        )

    async def _async_handle_hass_stop(self, _event: Event) -> None:
        """Abandon all sessions when Home Assistant stops."""
        await self.async_shutdown()

    def _remove_if_current(self, session: FindPhoneSession) -> None:
        """Remove a session without allowing an old task to remove its replacement."""
        if self.sessions.get(session.device_id) is session:
            self.sessions.pop(session.device_id, None)


def _registration_device_id(hass: HomeAssistant, target: AndroidTarget) -> str | None:
    """Return the Companion registration ID emitted in android intent events."""
    entry = (
        hass.data.get(MOBILE_APP_DOMAIN, {})
        .get(DATA_CONFIG_ENTRIES, {})
        .get(target.webhook_id)
    )
    if entry is None:
        return None
    device_id = entry.data.get(ATTR_DEVICE_ID)
    return device_id if isinstance(device_id, str) and device_id else None


def _keyguard_entity_id(hass: HomeAssistant, target: AndroidTarget) -> str | None:
    """Find the enabled Mobile App keyguard sensor by registry metadata."""
    registry = er.async_get(hass)
    expected_unique_id = f"{target.webhook_id}_{KEYGUARD_LOCKED_UNIQUE_ID}"
    matches = [
        entry.entity_id
        for entry in er.async_entries_for_device(registry, target.device_id)
        if entry.domain == "binary_sensor"
        and entry.platform == MOBILE_APP_DOMAIN
        and entry.unique_id == expected_unique_id
        and entry.disabled_by is None
    ]
    return matches[0] if len(matches) == 1 else None


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
