"""Tests for curated notifications and managed notification sessions."""

import asyncio
from types import SimpleNamespace

import pytest
import voluptuous as vol

from custom_components.android_device_control import notifications as module
from custom_components.android_device_control.const import (
    EVENT_NOTIFICATION_ACKNOWLEDGED,
    EVENT_NOTIFICATION_ACTION,
)
from custom_components.android_device_control.device import AndroidTarget
from custom_components.android_device_control.notifications import (
    AcknowledgementOptions,
    NotificationManager,
    notification_payload,
    validate_actions,
)


class FakeBus:
    def __init__(self) -> None:
        self.listeners = []
        self.fired = []

    def async_listen(self, event_type, listener):
        item = (event_type, listener)
        self.listeners.append(item)
        return lambda: self.listeners.remove(item) if item in self.listeners else None

    def async_listen_once(self, event_type, listener):
        return self.async_listen(event_type, listener)

    def async_fire(self, event_type, data) -> None:
        self.fired.append((event_type, data))


class FakeHass:
    def __init__(self) -> None:
        self.data = {}
        self.bus = FakeBus()

    def async_create_task(self, coro, name):
        return asyncio.create_task(coro, name=name)


class Recorder:
    def __init__(self) -> None:
        self.calls = []

    async def send(self, target, notification) -> None:
        self.calls.append((target, notification))


@pytest.fixture
def phone() -> AndroidTarget:
    return AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")


def ack_options(**overrides) -> AcknowledgementOptions:
    values = {
        "title": "Important",
        "message": "Check the door",
        "tag": "door",
        "channel": "Important",
        "acknowledgement_label": "Got it",
        "repeat_interval": 3600,
        "max_attempts": 3,
    }
    values.update(overrides)
    return AcknowledgementOptions(**values)


async def instant_timeout(awaitable, **_kwargs):
    awaitable.close()
    raise TimeoutError


def test_normal_and_urgent_notification_payloads() -> None:
    data = {
        "title": "Door",
        "message": "Open",
        "tag": "door",
        "channel": "Security",
        "importance": "high",
        "sticky": True,
        "timeout": 600,
    }
    normal = notification_payload(data)
    urgent = notification_payload(data, urgent=True)

    assert normal == {
        "title": "Door",
        "message": "Open",
        "data": {
            "tag": "door",
            "channel": "Security",
            "importance": "high",
            "sticky": "true",
            "timeout": 600,
        },
    }
    assert urgent["data"] | {"ttl": 0, "priority": "high"} == urgent["data"]


def test_action_validation_normalizes_and_rejects_unsafe_choices() -> None:
    assert validate_actions([{"id": "yes", "title": " Yes "}]) == [
        {"id": "yes", "title": "Yes"}
    ]
    for actions in (
        [],
        [{"id": "same", "title": "One"}, {"id": "same", "title": "Two"}],
        [{"id": "unsafe id", "title": "One"}],
        [{"id": "ok", "title": ""}],
        [{"id": str(index), "title": str(index)} for index in range(4)],
    ):
        with pytest.raises(vol.Invalid):
            validate_actions(actions)


async def test_concurrent_prompts_are_isolated(phone: AndroidTarget) -> None:
    hass = FakeHass()
    recorder = Recorder()
    manager = NotificationManager(hass, recorder.send)
    first = await manager.async_prompt(
        phone,
        title="First",
        message="Choose",
        tag="first",
        actions=validate_actions([{"id": "yes", "title": "Yes"}]),
    )
    second = await manager.async_prompt(
        phone,
        title="Second",
        message="Choose",
        tag="second",
        actions=validate_actions([{"id": "no", "title": "No"}]),
    )

    token = next(iter(second.actions_by_token))
    await manager._async_handle_action(SimpleNamespace(data={"action": token}))

    assert first.session_id in manager.prompts
    assert second.session_id not in manager.prompts
    assert hass.bus.fired == [
        (
            EVENT_NOTIFICATION_ACTION,
            {
                "device_id": "phone",
                "session_id": second.session_id,
                "action_id": "no",
                "tag": "second",
            },
        )
    ]


async def test_malformed_unrelated_and_stale_prompt_actions_are_ignored(
    phone: AndroidTarget,
) -> None:
    hass = FakeHass()
    manager = NotificationManager(hass, Recorder().send)
    session = await manager.async_prompt(
        phone,
        title="Question",
        message="Choose",
        tag=None,
        actions=validate_actions([{"id": "yes", "title": "Yes"}]),
    )

    for action in (None, 42, "OTHER", "ANDROID_DEVICE_CONTROL_ACTION_stale"):
        await manager._async_handle_action(SimpleNamespace(data={"action": action}))

    assert manager.prompts == {session.session_id: session}
    assert hass.bus.fired == []


async def test_acknowledgement_repeats_immediately_then_stops_at_maximum(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    recorder = Recorder()
    manager = NotificationManager(FakeHass(), recorder.send)
    monkeypatch.setattr(module.asyncio, "wait_for", instant_timeout)

    session = await manager.async_start_acknowledgement(phone, ack_options())
    await session.task

    assert session.attempts_sent == 3
    assert len(recorder.calls) == 3
    assert manager.acknowledgements == {}
    assert all(call[1]["data"]["ttl"] == 0 for call in recorder.calls)


async def test_acknowledgement_action_cancels_and_clears(phone: AndroidTarget) -> None:
    hass = FakeHass()
    recorder = Recorder()
    manager = NotificationManager(hass, recorder.send)
    session = await manager.async_start_acknowledgement(phone, ack_options())

    await manager._async_handle_action(
        SimpleNamespace(data={"action": session.action_token})
    )

    assert session.task.done()
    assert manager.acknowledgements == {}
    assert recorder.calls[-1][1] == {
        "message": "clear_notification",
        "data": {"tag": "door"},
    }
    assert hass.bus.fired[-1][0] == EVENT_NOTIFICATION_ACKNOWLEDGED
    assert hass.bus.fired[-1][1]["attempts"] == 1


async def test_explicit_stop_and_same_tag_replacement_are_safe(
    phone: AndroidTarget,
) -> None:
    recorder = Recorder()
    manager = NotificationManager(FakeHass(), recorder.send)
    old = await manager.async_start_acknowledgement(phone, ack_options())
    new = await manager.async_start_acknowledgement(phone, ack_options())

    await manager._async_handle_action(
        SimpleNamespace(data={"action": old.action_token})
    )
    assert manager.acknowledgements[new.key] is new

    assert await manager.async_stop_acknowledgement(phone, "door") is True
    assert manager.acknowledgements == {}
    assert await manager.async_stop_acknowledgement(phone, "door") is False


async def test_devices_have_independent_acknowledgement_sessions(
    phone: AndroidTarget,
) -> None:
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    manager = NotificationManager(FakeHass(), Recorder().send)
    phone_session = await manager.async_start_acknowledgement(phone, ack_options())
    tablet_session = await manager.async_start_acknowledgement(tablet, ack_options())

    await manager.async_stop_acknowledgement(phone, "door")

    assert phone_session.stop_event.is_set()
    assert not tablet_session.stop_event.is_set()
    await manager.async_shutdown()


async def test_shutdown_cancels_tasks_sessions_prompts_and_listeners(
    phone: AndroidTarget,
) -> None:
    hass = FakeHass()
    manager = NotificationManager(hass, Recorder().send)
    manager.async_register()
    acknowledgement = await manager.async_start_acknowledgement(phone, ack_options())
    await manager.async_prompt(
        phone,
        title="Question",
        message="Choose",
        tag=None,
        actions=validate_actions([{"id": "yes", "title": "Yes"}]),
    )

    await manager.async_shutdown()

    assert acknowledgement.task.cancelled()
    assert manager.acknowledgements == {}
    assert manager.prompts == {}
    assert hass.bus.listeners == []
