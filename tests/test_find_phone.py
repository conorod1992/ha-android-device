"""Tests for managed Find Phone sessions."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.android_device_control import find_phone as find_phone_module
from custom_components.android_device_control.const import (
    FIND_PHONE_EVENT_DEVICE_ID,
    FIND_PHONE_EVENT_SESSION_ID,
    FIND_PHONE_NOTIFICATION_ACTION_PREFIX,
    FIND_PHONE_NOTIFICATION_TAG,
)
from custom_components.android_device_control.device import (
    DATA_CONFIG_ENTRIES,
    MOBILE_APP_DOMAIN,
    AndroidTarget,
)
from custom_components.android_device_control.find_phone import (
    EVENT_ANDROID_INTENT_RECEIVED,
    USER_PRESENT_INTENT,
    FindPhoneManager,
    FindPhoneOptions,
    FindPhoneSession,
)


class FakeBus:
    def __init__(self) -> None:
        self.listeners = []

    def async_listen(self, event_type, listener):
        item = (event_type, listener)
        self.listeners.append(item)
        return lambda: self.listeners.remove(item) if item in self.listeners else None

    def async_listen_once(self, event_type, listener):
        return self.async_listen(event_type, listener)


class FakeHass:
    def __init__(self) -> None:
        self.data = {}
        self.bus = FakeBus()

    def async_create_task(self, coro, name):
        return asyncio.create_task(coro, name=name)


class Recorder:
    def __init__(self) -> None:
        self.calls = []
        self.fail_next_message = None

    async def send(self, target, command) -> None:
        self.calls.append((target, command))
        if command["message"] == self.fail_next_message:
            self.fail_next_message = None
            raise RuntimeError("transient push failure")


@pytest.fixture
def phone() -> AndroidTarget:
    return AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")


def options(**overrides) -> FindPhoneOptions:
    values = {
        "wake_screen": True,
        "flashlight": False,
        "sound_mode": "ringtone",
        "message": "Finding phone",
        "repeat": True,
        "max_attempts": 3,
        "repeat_interval": 3600,
        "show_stop_action": True,
        "stop_when_unlocked": True,
    }
    values.update(overrides)
    return FindPhoneOptions(**values)


async def instant_timeout(awaitable, **_kwargs):
    awaitable.close()
    raise TimeoutError


async def test_first_attempt_is_ordered_and_later_attempts_send_sound_only(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    monkeypatch.setattr(find_phone_module.asyncio, "wait_for", instant_timeout)

    session = await manager.async_start(phone, options(flashlight=True))
    await session.task

    assert [command["message"] for _, command in recorder.calls] == [
        "command_screen_on",
        "command_flashlight",
        "Finding phone",
        "Finding phone",
        "Finding phone",
        "clear_notification",
    ]
    assert session.attempts_sent == 3
    assert manager.sessions == {}


async def test_ringtone_payload_and_action_metadata_are_stable(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    monkeypatch.setattr(find_phone_module.asyncio, "wait_for", instant_timeout)

    session = await manager.async_start(phone, options(wake_screen=False))
    await session.task
    sounds = [
        command
        for _, command in recorder.calls
        if command["message"] == "Finding phone"
    ]

    assert len(sounds) == 3
    assert all(sound["data"]["ttl"] == 0 for sound in sounds)
    assert all(sound["data"]["priority"] == "high" for sound in sounds)
    assert all(sound["data"]["channel"] == "alarm_stream" for sound in sounds)
    assert {sound["data"]["tag"] for sound in sounds} == {FIND_PHONE_NOTIFICATION_TAG}
    assert {sound["data"]["actions"][0]["action"] for sound in sounds} == {
        f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}{session.session_id}"
    }


async def test_tts_uses_maximum_alarm_and_optional_control_notification(
    phone: AndroidTarget,
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)

    await manager.async_start(
        phone,
        options(
            sound_mode="tts",
            repeat=False,
            message="Find me",
            wake_screen=False,
        ),
    )

    assert [command["message"] for _, command in recorder.calls] == [
        "Finding phone",
        "TTS",
    ]
    assert recorder.calls[-1][1]["data"] == {
        "tts_text": "Find me",
        "media_stream": "alarm_stream_max",
    }
    assert recorder.calls[0][1]["data"]["actions"][0]["title"] == "Stop ringing"

    cleanup_start = len(recorder.calls)
    await manager.async_stop(phone)
    assert [command["message"] for _, command in recorder.calls[cleanup_start:]] == [
        "command_stop_tts",
        "clear_notification",
    ]
    recorder.calls.clear()
    await manager.async_start(
        phone,
        options(
            sound_mode="tts",
            repeat=False,
            wake_screen=False,
            show_stop_action=False,
        ),
    )
    assert [command["message"] for _, command in recorder.calls] == ["TTS"]
    await manager.async_shutdown()


async def test_stop_interrupts_wait_and_cleans_known_flashlight(
    phone: AndroidTarget,
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    session = await manager.async_start(phone, options(flashlight=True))
    await asyncio.sleep(0)

    await manager.async_stop(phone)

    assert session.task.done()
    assert session.attempts_sent == 1
    assert manager.sessions == {}
    assert recorder.calls[-1][1]["message"] == "clear_notification"
    assert not any(
        command["message"] in {"command_stop_tts", "command_flashlight"}
        for _, command in recorder.calls[3:]
    )


async def test_start_replaces_only_same_device(phone: AndroidTarget) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    first = await manager.async_start(phone, options())
    second = await manager.async_start(phone, options(repeat=False))

    assert first.stop_event.is_set()
    assert first.task.done()
    assert first.session_id != second.session_id
    assert manager.sessions == {phone.device_id: second}
    await manager.async_shutdown()


def test_old_session_cannot_remove_newer_session(phone: AndroidTarget) -> None:
    manager = FindPhoneManager(FakeHass(), Recorder().send)
    old = FindPhoneSession(
        phone.device_id,
        phone,
        "old",
        asyncio.Event(),
        options(),
    )
    new = FindPhoneSession(
        phone.device_id,
        phone,
        "new",
        asyncio.Event(),
        options(),
    )
    manager.sessions[phone.device_id] = new

    manager._remove_if_current(old)

    assert manager.sessions[phone.device_id] is new


async def test_devices_have_independent_sessions(phone: AndroidTarget) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    phone_session = await manager.async_start(phone, options())
    tablet_session = await manager.async_start(tablet, options())

    await manager.async_stop(phone)

    assert phone_session.stop_event.is_set()
    assert tablet_session.stop_event.is_set() is False
    assert manager.sessions == {"tablet": tablet_session}
    await manager.async_shutdown()


async def test_transient_failure_does_not_end_future_attempts(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    recorder.fail_next_message = "Finding phone"
    manager = FindPhoneManager(FakeHass(), recorder.send)
    monkeypatch.setattr(find_phone_module.asyncio, "wait_for", instant_timeout)

    session = await manager.async_start(phone, options(wake_screen=True))
    await session.task

    assert session.attempts_sent == 3
    assert len(recorder.calls) == 5
    assert manager.sessions == {}


async def test_matching_notification_action_stops_correct_session(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    session = await manager.async_start(phone, options())
    initial_count = len(recorder.calls)
    monkeypatch.setattr(
        find_phone_module,
        "resolve_android_targets",
        lambda *_args: pytest.fail("active sessions must not resolve targets again"),
    )
    event = SimpleNamespace(
        data={
            "action": (f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}{session.session_id}"),
        }
    )

    await manager._async_handle_notification_action(event)
    await asyncio.sleep(0)

    assert session.stop_event.is_set()
    assert session.task.done()
    assert session.attempts_sent == 1
    assert manager.sessions == {}
    assert [command["message"] for _, command in recorder.calls[initial_count:]] == [
        "clear_notification",
    ]


async def test_initial_total_failure_raises_and_leaves_no_session(
    phone: AndroidTarget,
) -> None:
    async def fail(_target, _command) -> None:
        raise RuntimeError("offline")

    manager = FindPhoneManager(FakeHass(), fail)

    with pytest.raises(HomeAssistantError):
        await manager.async_start(phone, options(wake_screen=False, repeat=False))

    assert manager.sessions == {}


async def test_initial_optional_failure_still_starts_when_sound_dispatches(
    phone: AndroidTarget,
) -> None:
    calls = []

    async def fail_wake_only(_target, command) -> None:
        calls.append(command["message"])
        if command["message"] == "command_screen_on":
            raise RuntimeError("wake unavailable")

    manager = FindPhoneManager(FakeHass(), fail_wake_only)
    session = await manager.async_start(phone, options(repeat=False))

    assert calls == ["command_screen_on", "Finding phone"]
    assert manager.sessions[phone.device_id] is session
    await manager.async_shutdown()


async def test_exact_action_stops_only_matching_session(
    phone: AndroidTarget,
) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    phone_session = await manager.async_start(phone, options())
    tablet_session = await manager.async_start(tablet, options())

    await manager._async_handle_notification_action(
        SimpleNamespace(
            data={
                "action": (
                    f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}"
                    f"{tablet_session.session_id}"
                )
            }
        )
    )

    assert tablet_session.stop_event.is_set()
    assert phone_session.stop_event.is_set() is False
    assert manager.sessions == {phone.device_id: phone_session}
    await manager.async_shutdown()


async def test_unmatched_action_stops_sole_session_as_fallback(
    caplog: pytest.LogCaptureFixture, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    session = await manager.async_start(phone, options())

    await manager._async_handle_notification_action(
        SimpleNamespace(
            data={"action": f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}altered"}
        )
    )

    assert session.stop_event.is_set()
    assert manager.sessions == {}
    assert "stopping the sole active session as a fallback" in caplog.text


async def test_unmatched_action_is_ignored_with_multiple_sessions(
    caplog: pytest.LogCaptureFixture, phone: AndroidTarget
) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    phone_session = await manager.async_start(phone, options())
    tablet_session = await manager.async_start(tablet, options())
    initial_count = len(recorder.calls)

    await manager._async_handle_notification_action(
        SimpleNamespace(
            data={"action": f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}altered"}
        )
    )

    assert phone_session.stop_event.is_set() is False
    assert tablet_session.stop_event.is_set() is False
    assert len(recorder.calls) == initial_count
    assert "metadata is insufficient" in caplog.text
    await manager.async_shutdown()


async def test_unrelated_or_stale_notification_action_is_ignored(
    phone: AndroidTarget,
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    session = await manager.async_start(phone, options())
    initial_count = len(recorder.calls)

    await manager._async_handle_notification_action(
        SimpleNamespace(data={"action": "SOME_OTHER_ACTION"})
    )
    await manager._async_handle_notification_action(
        SimpleNamespace(
            data={
                "action": f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}old",
                "tag": FIND_PHONE_NOTIFICATION_TAG,
                FIND_PHONE_EVENT_DEVICE_ID: phone.device_id,
                FIND_PHONE_EVENT_SESSION_ID: "old",
            }
        )
    )

    assert manager.sessions[phone.device_id] is session
    assert len(recorder.calls) == initial_count
    await manager.async_shutdown()


@pytest.mark.parametrize(
    "action",
    [
        FIND_PHONE_NOTIFICATION_ACTION_PREFIX,
        f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}   ",
    ],
)
async def test_malformed_find_phone_action_is_ignored(
    action: str, caplog: pytest.LogCaptureFixture, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    session = await manager.async_start(phone, options())
    initial_count = len(recorder.calls)

    await manager._async_handle_notification_action(
        SimpleNamespace(data={"action": action})
    )

    assert manager.sessions[phone.device_id] is session
    assert len(recorder.calls) == initial_count
    assert "malformed Find Phone stop action" in caplog.text
    await manager.async_shutdown()


async def test_action_after_restart_performs_conservative_device_cleanup(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    monkeypatch.setattr(
        find_phone_module,
        "resolve_android_targets",
        lambda _hass, _ids: [phone],
    )
    session_id = "session-before-restart"

    await manager._async_handle_notification_action(
        SimpleNamespace(
            data={
                "action": f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}{session_id}",
                "tag": FIND_PHONE_NOTIFICATION_TAG,
                FIND_PHONE_EVENT_DEVICE_ID: phone.device_id,
                FIND_PHONE_EVENT_SESSION_ID: session_id,
            }
        )
    )

    assert [command["message"] for _, command in recorder.calls] == [
        "clear_notification",
        "clear_notification",
    ]
    assert {command["data"]["tag"] for _, command in recorder.calls} == {
        FIND_PHONE_NOTIFICATION_TAG,
        "find_phone",
    }


async def test_shutdown_cancels_tasks_clears_sessions_and_listeners(
    phone: AndroidTarget,
) -> None:
    hass = FakeHass()
    manager = FindPhoneManager(hass, Recorder().send)
    manager.async_register()
    session = await manager.async_start(phone, options())

    await manager.async_shutdown()

    assert session.task.cancelled()
    assert manager.sessions == {}
    assert hass.bus.listeners == []


async def test_manager_recreation_does_not_restore_sessions(
    phone: AndroidTarget,
) -> None:
    hass = FakeHass()
    first_manager = FindPhoneManager(hass, Recorder().send)
    await first_manager.async_start(phone, options())
    await first_manager.async_shutdown()

    restarted_manager = FindPhoneManager(hass, Recorder().send)

    assert restarted_manager.sessions == {}


async def test_one_device_dispatch_failure_does_not_block_another(
    phone: AndroidTarget,
) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    successful_targets = []

    async def send(target, _command) -> None:
        if target.device_id == phone.device_id:
            raise RuntimeError("phone unavailable")
        successful_targets.append(target.device_id)

    manager = FindPhoneManager(FakeHass(), send)
    results = await asyncio.gather(
        manager.async_start(phone, options(repeat=False)),
        manager.async_start(tablet, options(repeat=False)),
        return_exceptions=True,
    )

    assert isinstance(results[0], HomeAssistantError)
    assert successful_targets == ["tablet", "tablet"]
    await manager.async_shutdown()


def add_registration(hass, target, registration_device_id):
    hass.data.setdefault(MOBILE_APP_DOMAIN, {}).setdefault(DATA_CONFIG_ENTRIES, {})[
        target.webhook_id
    ] = SimpleNamespace(data={"device_id": registration_device_id})


def state_change(old, new):
    return SimpleNamespace(
        data={
            "old_state": SimpleNamespace(state=old),
            "new_state": SimpleNamespace(state=new),
        }
    )


async def test_user_present_for_target_device_stops_active_session(
    phone: AndroidTarget,
) -> None:
    hass = FakeHass()
    add_registration(hass, phone, "companion-phone")
    recorder = Recorder()
    manager = FindPhoneManager(hass, recorder.send)
    session = await manager.async_start(phone, options())

    await manager._async_handle_android_intent(
        SimpleNamespace(
            data={"intent": USER_PRESENT_INTENT, "device_id": "companion-phone"}
        )
    )

    assert session.stop_event.is_set()
    assert manager.sessions == {}
    assert [command["message"] for _, command in recorder.calls[-1:]] == [
        "clear_notification",
    ]


@pytest.mark.parametrize(
    "event_data",
    [
        {"intent": USER_PRESENT_INTENT, "device_id": "companion-other"},
        {"intent": "android.intent.action.SCREEN_ON", "device_id": "companion-phone"},
        {"intent": USER_PRESENT_INTENT},
        {"intent": USER_PRESENT_INTENT, "device_id": ""},
    ],
)
async def test_unrelated_or_unidentifiable_android_intent_is_ignored(
    event_data, phone: AndroidTarget
) -> None:
    hass = FakeHass()
    add_registration(hass, phone, "companion-phone")
    manager = FindPhoneManager(hass, Recorder().send)
    session = await manager.async_start(phone, options())

    await manager._async_handle_android_intent(SimpleNamespace(data=event_data))

    assert manager.sessions == {phone.device_id: session}
    assert session.stop_event.is_set() is False
    await manager.async_shutdown()


async def test_user_present_stops_only_matching_simultaneous_session(
    phone: AndroidTarget,
) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    hass = FakeHass()
    add_registration(hass, phone, "companion-phone")
    add_registration(hass, tablet, "companion-tablet")
    manager = FindPhoneManager(hass, Recorder().send)
    phone_session = await manager.async_start(phone, options())
    tablet_session = await manager.async_start(tablet, options())

    await manager._async_handle_android_intent(
        SimpleNamespace(
            data={"intent": USER_PRESENT_INTENT, "device_id": "companion-phone"}
        )
    )

    assert phone_session.stop_event.is_set()
    assert tablet_session.stop_event.is_set() is False
    assert manager.sessions == {tablet.device_id: tablet_session}
    await manager.async_shutdown()


async def test_ambiguous_registration_device_id_is_ignored(
    phone: AndroidTarget,
) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    hass = FakeHass()
    add_registration(hass, phone, "duplicate-registration")
    add_registration(hass, tablet, "duplicate-registration")
    manager = FindPhoneManager(hass, Recorder().send)
    phone_session = await manager.async_start(phone, options())
    tablet_session = await manager.async_start(tablet, options())

    await manager._async_handle_android_intent(
        SimpleNamespace(
            data={
                "intent": USER_PRESENT_INTENT,
                "device_id": "duplicate-registration",
            }
        )
    )

    assert manager.sessions == {
        phone.device_id: phone_session,
        tablet.device_id: tablet_session,
    }
    await manager.async_shutdown()


async def test_keyguard_locked_to_unlocked_stops_corresponding_session(
    phone: AndroidTarget,
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    session = await manager.async_start(phone, options())

    await manager._async_handle_keyguard_state(session, state_change("on", "off"))

    assert session.stop_event.is_set()
    assert manager.sessions == {}


async def test_keyguard_unlock_stops_only_its_device(
    phone: AndroidTarget,
) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    manager = FindPhoneManager(FakeHass(), Recorder().send)
    phone_session = await manager.async_start(phone, options())
    tablet_session = await manager.async_start(tablet, options())

    await manager._async_handle_keyguard_state(
        tablet_session, state_change("on", "off")
    )

    assert tablet_session.stop_event.is_set()
    assert phone_session.stop_event.is_set() is False
    assert manager.sessions == {phone.device_id: phone_session}
    await manager.async_shutdown()


@pytest.mark.parametrize(
    ("old_state", "new_state"),
    [("off", "on"), ("on", "unavailable"), ("unavailable", "off")],
)
async def test_non_unlock_keyguard_updates_do_not_stop_find_phone(
    old_state, new_state, phone: AndroidTarget
) -> None:
    manager = FindPhoneManager(FakeHass(), Recorder().send)
    session = await manager.async_start(phone, options())

    await manager._async_handle_keyguard_state(
        session, state_change(old_state, new_state)
    )

    assert manager.sessions == {phone.device_id: session}
    await manager.async_shutdown()


async def test_keyguard_update_for_old_session_cannot_stop_replacement(
    phone: AndroidTarget,
) -> None:
    manager = FindPhoneManager(FakeHass(), Recorder().send)
    old_session = await manager.async_start(phone, options())
    new_session = await manager.async_start(phone, options())

    await manager._async_handle_keyguard_state(old_session, state_change("on", "off"))

    assert manager.sessions == {phone.device_id: new_session}
    assert new_session.stop_event.is_set() is False
    await manager.async_shutdown()


async def test_auto_stop_disabled_ignores_intent_and_keyguard(
    phone: AndroidTarget,
) -> None:
    hass = FakeHass()
    add_registration(hass, phone, "companion-phone")
    manager = FindPhoneManager(hass, Recorder().send)
    session = await manager.async_start(phone, options(stop_when_unlocked=False))

    await manager._async_handle_android_intent(
        SimpleNamespace(
            data={"intent": USER_PRESENT_INTENT, "device_id": "companion-phone"}
        )
    )
    await manager._async_handle_keyguard_state(session, state_change("on", "off"))

    assert manager.sessions == {phone.device_id: session}
    assert session.stop_event.is_set() is False
    await manager.async_shutdown()


async def test_simultaneous_unlock_signals_cleanup_once(phone: AndroidTarget) -> None:
    hass = FakeHass()
    add_registration(hass, phone, "companion-phone")
    recorder = Recorder()
    manager = FindPhoneManager(hass, recorder.send)
    session = await manager.async_start(phone, options())
    initial_count = len(recorder.calls)

    await asyncio.gather(
        manager._async_handle_android_intent(
            SimpleNamespace(
                data={"intent": USER_PRESENT_INTENT, "device_id": "companion-phone"}
            )
        ),
        manager._async_handle_keyguard_state(session, state_change("on", "off")),
    )

    assert [command["message"] for _, command in recorder.calls[initial_count:]] == [
        "clear_notification",
    ]


async def test_notification_stop_and_unlock_cleanup_once(
    phone: AndroidTarget,
) -> None:
    hass = FakeHass()
    add_registration(hass, phone, "companion-phone")
    recorder = Recorder()
    manager = FindPhoneManager(hass, recorder.send)
    session = await manager.async_start(phone, options())
    initial_count = len(recorder.calls)

    await asyncio.gather(
        manager._async_handle_android_intent(
            SimpleNamespace(
                data={"intent": USER_PRESENT_INTENT, "device_id": "companion-phone"}
            )
        ),
        manager._async_handle_notification_action(
            SimpleNamespace(
                data={
                    "action": (
                        f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}{session.session_id}"
                    )
                }
            )
        ),
    )

    assert [command["message"] for _, command in recorder.calls[initial_count:]] == [
        "clear_notification",
    ]


async def test_keyguard_listener_is_scoped_and_removed(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    hass = FakeHass()
    hass.states = SimpleNamespace()
    subscriptions = []

    monkeypatch.setattr(
        find_phone_module,
        "_keyguard_entity_id",
        lambda _hass, _target: "binary_sensor.pixel_9_keyguard_locked",
    )

    def track(_hass, entity_ids, listener):
        subscription = {"entity_ids": entity_ids, "listener": listener, "active": True}
        subscriptions.append(subscription)

        def unsubscribe():
            subscription["active"] = False

        return unsubscribe

    monkeypatch.setattr(find_phone_module, "async_track_state_change_event", track)
    manager = FindPhoneManager(hass, Recorder().send)
    manager.async_register()
    session = await manager.async_start(phone, options())

    assert subscriptions[0]["entity_ids"] == ["binary_sensor.pixel_9_keyguard_locked"]
    assert subscriptions[0]["active"] is True
    assert [event_type for event_type, _ in hass.bus.listeners].count(
        EVENT_ANDROID_INTENT_RECEIVED
    ) == 1

    await manager.async_stop(phone)

    assert subscriptions[0]["active"] is False
    assert session.keyguard_unsubscribe is None
    await manager.async_shutdown()
    assert hass.bus.listeners == []


def test_keyguard_discovery_uses_device_registry_metadata(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    entries = [
        SimpleNamespace(
            entity_id="binary_sensor.pixel_9_interactive",
            domain="binary_sensor",
            platform=MOBILE_APP_DOMAIN,
            unique_id="webhook-phone_interactive",
            disabled_by=None,
        ),
        SimpleNamespace(
            entity_id="binary_sensor.pixel_9_keyguard_locked",
            domain="binary_sensor",
            platform=MOBILE_APP_DOMAIN,
            unique_id="webhook-phone_keyguard_locked",
            disabled_by=None,
        ),
        SimpleNamespace(
            entity_id="binary_sensor.other_keyguard_locked",
            domain="binary_sensor",
            platform=MOBILE_APP_DOMAIN,
            unique_id="webhook-other_keyguard_locked",
            disabled_by=None,
        ),
    ]
    registry = object()
    monkeypatch.setattr(find_phone_module.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        find_phone_module.er,
        "async_entries_for_device",
        lambda actual_registry, device_id: (
            entries
            if actual_registry is registry and device_id == phone.device_id
            else pytest.fail("wrong device registry lookup")
        ),
    )

    assert (
        find_phone_module._keyguard_entity_id(FakeHass(), phone)
        == "binary_sensor.pixel_9_keyguard_locked"
    )
