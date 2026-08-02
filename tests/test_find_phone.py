"""Tests for managed Find Phone sessions."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.android_device_control import find_phone as find_phone_module
from custom_components.android_device_control.const import (
    FIND_PHONE_EVENT_DEVICE_ID,
    FIND_PHONE_EVENT_SESSION_ID,
    FIND_PHONE_NOTIFICATION_ACTION_PREFIX,
    FIND_PHONE_NOTIFICATION_TAG,
)
from custom_components.android_device_control.device import AndroidTarget
from custom_components.android_device_control.find_phone import (
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
    sounds = [command for _, command in recorder.calls]

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
    assert [command["message"] for _, command in recorder.calls[-3:]] == [
        "command_stop_tts",
        "clear_notification",
        "command_flashlight",
    ]
    assert recorder.calls[-1][1]["data"]["command"] == "turn_off"


async def test_start_replaces_only_same_device(phone: AndroidTarget) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    first = await manager.async_start(phone, options())
    second = await manager.async_start(phone, options(repeat=False))

    assert first.stop_event.is_set()
    assert first.task.done()
    assert first.session_id != second.session_id
    assert manager.sessions == {}


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

    session = await manager.async_start(phone, options(wake_screen=False))
    await session.task

    assert session.attempts_sent == 3
    assert len(recorder.calls) == 3
    assert manager.sessions == {}


async def test_matching_notification_action_stops_correct_session(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    session = await manager.async_start(phone, options())
    monkeypatch.setattr(
        find_phone_module,
        "resolve_android_targets",
        lambda _hass, _ids: [phone],
    )
    event = SimpleNamespace(
        data={
            "action": (f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}{session.session_id}"),
            "tag": FIND_PHONE_NOTIFICATION_TAG,
            FIND_PHONE_EVENT_DEVICE_ID: phone.device_id,
            FIND_PHONE_EVENT_SESSION_ID: session.session_id,
        }
    )

    await manager._async_handle_notification_action(event)

    assert session.stop_event.is_set()
    assert manager.sessions == {}
    assert [command["message"] for _, command in recorder.calls[-2:]] == [
        "command_stop_tts",
        "clear_notification",
    ]


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
        "command_stop_tts",
        "clear_notification",
    ]


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
    await asyncio.gather(
        manager.async_start(phone, options(repeat=False)),
        manager.async_start(tablet, options(repeat=False)),
    )

    assert successful_targets == ["tablet", "tablet"]
