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


class FakeStore:
    def __init__(self, data=None) -> None:
        self.data = data

    async def async_load(self):
        return self.data

    async def async_save(self, data) -> None:
        self.data = data


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
    assert len(recorder.calls) == 4
    assert manager.acknowledgements == {}
    assert all(call[1]["data"]["ttl"] == 0 for call in recorder.calls[:3])
    assert recorder.calls[-1][1]["message"] == "clear_notification"


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


async def test_prompt_action_mapping_survives_restart(phone: AndroidTarget) -> None:
    store = FakeStore()
    first = NotificationManager(FakeHass(), Recorder().send, store)
    prompt = await first.async_prompt(
        phone,
        title="Question",
        message="Continue?",
        tag="question",
        actions=[{"id": "yes", "title": "Yes"}],
    )
    token = next(iter(prompt.actions_by_token))
    await first.async_shutdown()

    hass = FakeHass()
    restarted = NotificationManager(hass, Recorder().send, store)
    await restarted._async_handle_action(SimpleNamespace(data={"action": token}))

    assert hass.bus.fired[-1] == (
        EVENT_NOTIFICATION_ACTION,
        {
            "device_id": phone.device_id,
            "session_id": prompt.session_id,
            "action_id": "yes",
            "tag": "question",
        },
    )


async def test_stale_and_malformed_persisted_prompts_are_pruned(
    phone: AndroidTarget,
) -> None:
    store = FakeStore(
        {
            "prompts": [
                {
                    "session_id": "expired",
                    "target": module._target_to_dict(phone),
                    "tag": None,
                    "actions_by_token": {"ANDROID_DEVICE_CONTROL_ACTION_old": "yes"},
                    "created_at": 0,
                },
                {"malformed": True},
            ],
            "acknowledgements": "invalid",
        }
    )
    manager = NotificationManager(FakeHass(), Recorder().send, store)

    await manager._async_ensure_loaded()

    assert manager.prompts == {}
    assert store.data == {"prompts": [], "acknowledgements": []}


async def test_acknowledgement_restart_clears_and_recognizes_late_action(
    phone: AndroidTarget,
) -> None:
    store = FakeStore()
    first = NotificationManager(FakeHass(), Recorder().send, store)
    session = await first.async_start_acknowledgement(phone, ack_options())
    await first.async_shutdown()

    hass = FakeHass()
    recorder = Recorder()
    restarted = NotificationManager(hass, recorder.send, store)
    await restarted._async_ensure_loaded()
    await restarted._async_handle_action(
        SimpleNamespace(data={"action": session.action_token})
    )

    assert recorder.calls[-1][1]["message"] == "clear_notification"
    assert hass.bus.fired[-1][1]["reconciled_after_restart"] is True


async def test_failed_acknowledgement_replacement_preserves_old_session(
    phone: AndroidTarget,
) -> None:
    recorder = Recorder()
    manager = NotificationManager(FakeHass(), recorder.send)
    old = await manager.async_start_acknowledgement(phone, ack_options())

    async def fail_replacement(_target, _notification) -> None:
        raise RuntimeError("push unavailable")

    manager._send = fail_replacement
    with pytest.raises(RuntimeError, match="push unavailable"):
        await manager.async_start_acknowledgement(phone, ack_options(message="new"))

    assert manager.acknowledgements[old.key] is old
    assert not old.stop_event.is_set()
    await manager.async_shutdown()


async def test_concurrent_same_tag_replacements_leave_one_task(
    phone: AndroidTarget,
) -> None:
    manager = NotificationManager(FakeHass(), Recorder().send)
    first, second = await asyncio.gather(
        manager.async_start_acknowledgement(phone, ack_options(message="first")),
        manager.async_start_acknowledgement(phone, ack_options(message="second")),
    )

    current = manager.acknowledgements[first.key]
    assert current is first or current is second
    assert sum(not item.task.done() for item in (first, second)) == 1
    await manager.async_shutdown()


async def test_old_repeat_cannot_overwrite_successful_replacement(
    monkeypatch: pytest.MonkeyPatch, phone: AndroidTarget
) -> None:
    old_repeat_waiting = asyncio.Event()
    release_old_repeat = asyncio.Event()
    replacement_sent = asyncio.Event()
    release_replacement_send = asyncio.Event()
    original_wait_for = module.asyncio.wait_for
    wait_calls = 0
    sent_tokens = []

    async def controlled_wait_for(awaitable, **kwargs):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            old_repeat_waiting.set()
            await release_old_repeat.wait()
            awaitable.close()
            raise TimeoutError
        return await original_wait_for(awaitable, **kwargs)

    async def controlled_send(_target, notification) -> None:
        token = notification["data"]["actions"][0]["action"]
        sent_tokens.append(token)
        if len(sent_tokens) == 2:
            replacement_sent.set()
            await release_replacement_send.wait()

    monkeypatch.setattr(module.asyncio, "wait_for", controlled_wait_for)
    manager = NotificationManager(FakeHass(), controlled_send)
    old = await manager.async_start_acknowledgement(phone, ack_options())
    await old_repeat_waiting.wait()

    replacement_task = asyncio.create_task(
        manager.async_start_acknowledgement(phone, ack_options(message="new"))
    )
    await replacement_sent.wait()
    release_old_repeat.set()
    await asyncio.sleep(0)
    release_replacement_send.set()
    replacement = await replacement_task

    assert manager.acknowledgements[old.key] is replacement
    assert old.task.done()
    assert not replacement.task.done()
    assert sent_tokens == [old.action_token, replacement.action_token]
    assert sent_tokens[-1] == replacement.action_token
    await manager.async_shutdown()
