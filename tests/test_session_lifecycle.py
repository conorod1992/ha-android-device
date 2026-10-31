"""Regressions for overlapping sends, cleanup, expiry, and unload."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.async_ import create_eager_task

from custom_components.android_device_control import find_phone as find_module
from custom_components.android_device_control import notifications as notify_module
from custom_components.android_device_control.const import (
    FIND_PHONE_EVENT_DEVICE_ID,
    FIND_PHONE_EVENT_SESSION_ID,
    FIND_PHONE_NOTIFICATION_ACTION_PREFIX,
    FIND_PHONE_NOTIFICATION_TAG,
)
from custom_components.android_device_control.device import AndroidTarget
from custom_components.android_device_control.find_phone import FindPhoneManager
from custom_components.android_device_control.notifications import NotificationManager

from .test_find_phone import options
from .test_notifications import FakeHass, FakeStore, Recorder, ack_options

PHONE = AndroidTarget("phone", "Pixel", "webhook-phone", "mobile_app_phone")
TABLET = AndroidTarget("tablet", "Tablet", "webhook-tablet", "mobile_app_tablet")


@pytest.fixture(params=[False, True], autouse=True)
def scheduling(request, monkeypatch):
    if request.param:
        monkeypatch.setattr(
            FakeHass,
            "async_create_background_task",
            lambda _self, coro, name: create_eager_task(coro, name=name),
        )


class Gate:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def pause(self):
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 2)

    async def wait(self):
        await asyncio.wait_for(self.entered.wait(), 2)


@pytest.fixture
async def resources():
    managers, gates = [], []
    yield managers, gates
    for gate in gates:
        gate.release.set()
    for manager in managers:
        await manager.async_shutdown()


async def test_find_phone_concurrent_replacements_leave_one_controllable_task(
    resources,
):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)
    calls = []

    async def send(_target, command):
        calls.append(command["message"])
        if command["message"] == "clear_notification":
            await gate.pause()

    manager = FindPhoneManager(FakeHass(), send)
    managers.append(manager)
    old = await manager.async_start(PHONE, options(stop_when_unlocked=False))
    first = asyncio.create_task(manager.async_start(PHONE, options()))
    await gate.wait()
    second = asyncio.create_task(manager.async_start(PHONE, options()))
    gate.release.set()
    one, two = await asyncio.wait_for(asyncio.gather(first, second), 2)

    assert manager.sessions == {PHONE.device_id: two}
    assert old.task.done()
    assert one.task.done()
    assert not two.task.done()
    await manager.async_stop(PHONE)
    assert all(session.task.done() for session in (old, one, two))
    assert calls[-1] == "clear_notification"


async def test_find_phone_stop_waits_for_initial_sound_before_clearing(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)
    calls = []

    async def send(_target, command):
        if command["message"] == "Finding phone":
            await gate.pause()
        calls.append(command["message"])

    manager = FindPhoneManager(FakeHass(), send)
    managers.append(manager)
    start = asyncio.create_task(manager.async_start(PHONE, options(wake_screen=False)))
    await gate.wait()
    stop = asyncio.create_task(manager.async_stop(PHONE))
    await asyncio.sleep(0)
    assert not stop.done()
    gate.release.set()
    session, _ = await asyncio.wait_for(asyncio.gather(start, stop), 2)
    assert calls == ["Finding phone", "clear_notification"]
    assert session.task.done()
    assert not manager.sessions


async def test_find_phone_slow_device_does_not_block_another(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)

    async def send(target, _command):
        if target == PHONE:
            await gate.pause()

    manager = FindPhoneManager(FakeHass(), send)
    managers.append(manager)
    start = asyncio.create_task(manager.async_start(PHONE, options()))
    await gate.wait()
    tablet = await asyncio.wait_for(manager.async_start(TABLET, options()), 1)
    assert manager.sessions[TABLET.device_id] is tablet
    gate.release.set()
    await start


async def test_cancelled_initial_find_phone_does_not_leave_session(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)

    async def send(_target, command):
        if command["message"] == "Finding phone":
            await gate.pause()

    manager = FindPhoneManager(FakeHass(), send)
    managers.append(manager)
    start = asyncio.create_task(manager.async_start(PHONE, options()))
    await gate.wait()
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    assert not manager.sessions


async def test_unload_during_initial_dispatch_does_not_create_repeat(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)

    async def send(_target, command):
        if command["message"] == "Finding phone":
            await gate.pause()

    manager = FindPhoneManager(FakeHass(), send)
    managers.append(manager)
    start = asyncio.create_task(manager.async_start(PHONE, options()))
    await gate.wait()
    shutdown = asyncio.create_task(manager.async_shutdown())
    await asyncio.sleep(0)
    gate.release.set()
    with pytest.raises(HomeAssistantError):
        await start
    await asyncio.wait_for(shutdown, 2)
    assert not manager.sessions
    with pytest.raises(HomeAssistantError):
        await manager.async_start(PHONE, options())


async def test_unload_during_replacement_cleanup_does_not_send_new_sound(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)
    calls = []

    async def send(_target, command):
        calls.append(command["message"])
        if command["message"] == "clear_notification":
            await gate.pause()

    manager = FindPhoneManager(FakeHass(), send)
    managers.append(manager)
    await manager.async_start(PHONE, options(wake_screen=False))
    start = asyncio.create_task(manager.async_start(PHONE, options(wake_screen=False)))
    await gate.wait()
    shutdown = asyncio.create_task(manager.async_shutdown())
    await asyncio.sleep(0)
    gate.release.set()
    with pytest.raises(HomeAssistantError):
        await start
    await asyncio.wait_for(shutdown, 2)
    assert calls == ["Finding phone", "clear_notification"]
    assert not manager.sessions


async def test_unload_during_prompt_send_preserves_existing_persisted_mapping(
    resources,
):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)

    async def send(_target, notification):
        if notification["message"] == "Pending":
            await gate.pause()

    store = FakeStore()
    manager = NotificationManager(FakeHass(), send, store)
    managers.append(manager)
    old = await manager.async_prompt(
        PHONE,
        title="Question",
        message="Existing",
        tag=None,
        actions=[{"id": "yes", "title": "Yes"}],
    )
    pending = asyncio.create_task(
        manager.async_prompt(
            PHONE,
            title="Question",
            message="Pending",
            tag=None,
            actions=[{"id": "yes", "title": "Yes"}],
        )
    )
    await gate.wait()
    await manager.async_shutdown()
    gate.release.set()
    with pytest.raises(HomeAssistantError):
        await pending
    assert [item["session_id"] for item in store.data["prompts"]] == [old.session_id]


async def test_stale_tablet_action_cannot_stop_phone(monkeypatch, resources):
    managers, _ = resources
    recorder = Recorder()
    manager = FindPhoneManager(FakeHass(), recorder.send)
    managers.append(manager)
    phone = await manager.async_start(PHONE, options())
    monkeypatch.setattr(find_module, "resolve_android_targets", lambda *_: [TABLET])
    recorder.calls.clear()
    await manager._async_handle_notification_action(
        SimpleNamespace(
            data={
                "action": f"{FIND_PHONE_NOTIFICATION_ACTION_PREFIX}old-tablet",
                FIND_PHONE_EVENT_DEVICE_ID: TABLET.device_id,
                FIND_PHONE_EVENT_SESSION_ID: "old-tablet",
                "tag": FIND_PHONE_NOTIFICATION_TAG,
            }
        )
    )
    assert manager.sessions == {PHONE.device_id: phone}
    assert not phone.stop_event.is_set()
    assert all(target == TABLET for target, _ in recorder.calls)


async def test_acknowledgement_stop_clear_precedes_replacement(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)
    calls = []

    async def send(_target, notification):
        if notification["message"] == "clear_notification":
            await gate.pause()
        calls.append(notification["message"])

    manager = NotificationManager(FakeHass(), send)
    managers.append(manager)
    await manager.async_start_acknowledgement(PHONE, ack_options(message="old"))
    stop = asyncio.create_task(manager.async_stop_acknowledgement(PHONE, "door"))
    await gate.wait()
    start = asyncio.create_task(
        manager.async_start_acknowledgement(PHONE, ack_options(message="new"))
    )
    gate.release.set()
    _, new = await asyncio.wait_for(asyncio.gather(stop, start), 2)
    assert calls == ["old", "clear_notification", "new"]
    assert manager.acknowledgements[new.key] is new


async def test_old_acknowledgement_click_cannot_clear_replacement(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)
    hass = FakeHass()
    calls = []

    async def send(_target, notification):
        if notification["message"] == "new":
            await gate.pause()
        calls.append(notification["message"])

    manager = NotificationManager(hass, send)
    managers.append(manager)
    old = await manager.async_start_acknowledgement(PHONE, ack_options(message="old"))
    start = asyncio.create_task(
        manager.async_start_acknowledgement(PHONE, ack_options(message="new"))
    )
    await gate.wait()
    action = asyncio.create_task(
        manager._async_handle_action(SimpleNamespace(data={"action": old.action_token}))
    )
    await asyncio.sleep(0)
    gate.release.set()
    new, _ = await asyncio.wait_for(asyncio.gather(start, action), 2)
    assert calls == ["old", "new"]
    assert not hass.bus.fired
    assert manager.acknowledgements[new.key] is new


async def test_expired_old_acknowledgement_cannot_clear_new_owner(resources):
    managers, _ = resources
    recorder = Recorder()
    manager = NotificationManager(FakeHass(), recorder.send)
    managers.append(manager)
    old = await manager.async_start_acknowledgement(PHONE, ack_options(message="old"))
    new = await manager.async_start_acknowledgement(PHONE, ack_options(message="new"))
    assert not await manager._async_stop_acknowledgement(old, clear=True)
    assert [item["message"] for _, item in recorder.calls] == ["old", "new"]
    assert manager.acknowledgements[new.key] is new


async def test_acknowledgement_slow_target_does_not_block_another(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)

    async def send(target, _notification):
        if target == PHONE:
            await gate.pause()

    manager = NotificationManager(FakeHass(), send)
    managers.append(manager)
    start = asyncio.create_task(
        manager.async_start_acknowledgement(PHONE, ack_options())
    )
    await gate.wait()
    await asyncio.wait_for(
        manager.async_start_acknowledgement(TABLET, ack_options()), 1
    )
    gate.release.set()
    await start


@pytest.mark.parametrize("expired", [False, True])
async def test_prompt_lifetime_during_uptime(monkeypatch, resources, expired):
    managers, _ = resources
    hass = FakeHass()
    store = FakeStore()
    manager = NotificationManager(hass, Recorder().send, store)
    managers.append(manager)
    prompt = await manager.async_prompt(
        PHONE,
        title="Question",
        message="Continue?",
        tag=None,
        actions=[{"id": "yes", "title": "Yes"}],
    )
    monkeypatch.setattr(
        notify_module.time,
        "time",
        lambda: prompt.created_at + notify_module.SESSION_TTL + (1 if expired else -1),
    )
    await manager._async_handle_action(
        SimpleNamespace(data={"action": next(iter(prompt.actions_by_token))})
    )
    assert bool(hass.bus.fired) is not expired
    assert not store.data["prompts"]


async def test_restored_acknowledgement_expires_during_uptime(monkeypatch, resources):
    managers, _ = resources
    hass, store = FakeHass(), FakeStore()
    first = NotificationManager(hass, Recorder().send, store)
    managers.append(first)
    old = await first.async_start_acknowledgement(PHONE, ack_options())
    await first.async_shutdown()
    restarted = NotificationManager(hass, Recorder().send, store)
    managers.append(restarted)
    await restarted._async_ensure_loaded()
    monkeypatch.setattr(
        notify_module.time,
        "time",
        lambda: old.created_at + notify_module.SESSION_TTL + 1,
    )
    await restarted._async_handle_action(
        SimpleNamespace(data={"action": old.action_token})
    )
    assert not hass.bus.fired
    assert not store.data["acknowledgements"]


async def test_unload_cancels_restore_and_rejects_queued_first_send(resources):
    managers, gates = resources
    gate = Gate()
    gates.append(gate)
    recorder = Recorder()

    class SlowStore(FakeStore):
        async def async_load(self):
            await gate.pause()
            return await super().async_load()

    manager = NotificationManager(FakeHass(), recorder.send, SlowStore())
    managers.append(manager)
    manager.async_register()
    await gate.wait()
    start = asyncio.create_task(
        manager.async_start_acknowledgement(PHONE, ack_options())
    )
    await asyncio.sleep(0)
    await asyncio.wait_for(manager.async_shutdown(), 1)
    with pytest.raises(HomeAssistantError):
        await start
    assert not recorder.calls
    assert not manager.acknowledgements
