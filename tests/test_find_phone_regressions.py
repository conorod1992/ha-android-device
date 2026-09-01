"""Regression tests for Find Phone correctness fixes."""

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.android_device_control.device import AndroidTarget
from custom_components.android_device_control.find_phone import (
    FindPhoneManager,
    FindPhoneOptions,
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


def _phone() -> AndroidTarget:
    return AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")


def _options(**overrides) -> FindPhoneOptions:
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


async def test_active_stop_honours_explicit_flashlight_cleanup() -> None:
    calls = []

    async def send(_target, command) -> None:
        calls.append(command)

    phone = _phone()
    manager = FindPhoneManager(FakeHass(), send)
    session = await manager.async_start(phone, _options(flashlight=True))
    await asyncio.sleep(0)

    await manager.async_stop(phone, turn_off_flashlight=True)

    assert session.task is not None
    assert session.task.done()
    assert manager.sessions == {}
    assert [command["message"] for command in calls[-2:]] == [
        "clear_notification",
        "command_flashlight",
    ]
    assert calls[-1]["data"]["command"] == "turn_off"


async def test_one_shot_requires_the_audible_command_to_dispatch() -> None:
    calls = []

    async def fail_sound(_target, command) -> None:
        calls.append(command["message"])
        if command["message"] == "Finding phone":
            raise RuntimeError("sound push failed")

    phone = _phone()
    manager = FindPhoneManager(FakeHass(), fail_sound)

    with pytest.raises(HomeAssistantError):
        await manager.async_start(phone, _options(repeat=False))

    assert calls == ["command_screen_on", "Finding phone"]
    assert manager.sessions == {}


async def test_repeating_session_can_retry_after_initial_sound_failure() -> None:
    calls = []
    failed_once = False

    async def fail_first_sound(_target, command) -> None:
        nonlocal failed_once
        calls.append(command["message"])
        if command["message"] == "Finding phone" and not failed_once:
            failed_once = True
            raise RuntimeError("transient sound push failure")

    phone = _phone()
    manager = FindPhoneManager(FakeHass(), fail_first_sound)
    session = await manager.async_start(phone, _options())

    assert manager.sessions == {phone.device_id: session}
    assert session.attempts_sent == 1
    assert calls == ["command_screen_on", "Finding phone"]

    await manager.async_shutdown()
