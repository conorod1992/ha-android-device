"""Exercise real Home Assistant services, background work, and storage."""

import asyncio
import json
from types import MappingProxyType

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant

from custom_components.android_device_control import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.android_device_control.const import (
    DATA_FIND_PHONE_MANAGER,
    DATA_NOTIFICATION_MANAGER,
    DOMAIN,
    EVENT_MOBILE_APP_NOTIFICATION_ACTION,
    EVENT_MOBILE_APP_NOTIFICATION_RECEIVED,
    EVENT_NOTIFICATION_ACTION,
)
from custom_components.android_device_control.device import AndroidTarget
from custom_components.android_device_control.notifications import STORE_KEY
from custom_components.android_device_control.services import async_unregister_services

PHONE = AndroidTarget("phone", "Pixel", "webhook-phone", "mobile_app_phone")


def entry():
    return ConfigEntry(
        data={},
        options={},
        domain=DOMAIN,
        source="user",
        title="Android",
        unique_id=DOMAIN,
        version=1,
        minor_version=1,
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )


@pytest.fixture
async def server(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    calls = []

    async def notify(call):
        calls.append(call)

    hass.services.async_register("notify", PHONE.notify_service, notify)
    monkeypatch.setattr(
        "custom_components.android_device_control.services.resolve_android_targets",
        lambda *_: [PHONE],
    )
    yield hass, calls
    await async_unregister_services(hass)
    await hass.async_block_till_done()
    hass.import_executor.shutdown(wait=False)


async def test_entry_background_tasks_do_not_block_idle_and_unload_cancels(server):
    hass, calls = server
    config_entry = entry()
    assert await async_setup_entry(hass, config_entry)
    notifications = hass.data[DOMAIN][DATA_NOTIFICATION_MANAGER]
    await notifications._async_ensure_loaded()
    context = Context(user_id="initiating-user")
    await hass.services.async_call(
        DOMAIN,
        "find_phone",
        {"device_id": "phone", "stop_when_unlocked": False, "wake_screen": False},
        blocking=True,
        context=context,
    )
    await hass.services.async_call(
        DOMAIN,
        "notify_until_acknowledged",
        {"device_id": "phone", "title": "Door", "message": "Check it"},
        blocking=True,
        context=context,
    )
    find = hass.data[DOMAIN][DATA_FIND_PHONE_MANAGER].sessions["phone"]
    acknowledgement = next(iter(notifications.acknowledgements.values()))
    assert find.task in config_entry._background_tasks
    assert acknowledgement.task in config_entry._background_tasks
    await asyncio.wait_for(hass.async_block_till_done(), 1)
    assert all(call.context is context for call in calls)
    assert all(call.data["target"] == [PHONE.webhook_id] for call in calls)

    assert await async_unload_entry(hass, config_entry)
    assert find.task.cancelled()
    assert acknowledgement.task.cancelled()
    assert not hass.services.has_service(DOMAIN, "find_phone")
    assert DOMAIN not in hass.data
    listeners = hass.bus.async_listeners()
    assert EVENT_MOBILE_APP_NOTIFICATION_ACTION not in listeners
    assert EVENT_MOBILE_APP_NOTIFICATION_RECEIVED not in listeners


async def test_real_service_validation_rejects_before_dispatch(server):
    hass, calls = server
    config_entry = entry()
    await async_setup_entry(hass, config_entry)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_screen_brightness",
            {"device_id": "phone", "brightness": 101},
            blocking=True,
        )
    assert not calls


async def test_repeat_retains_initiating_context_after_service_returns(server):
    hass, calls = server
    await async_setup_entry(hass, entry())
    context = Context(user_id="repeat-initiator")
    await hass.services.async_call(
        DOMAIN,
        "find_phone",
        {
            "device_id": "phone",
            "stop_when_unlocked": False,
            "wake_screen": False,
            "repeat_interval": {"seconds": 3},
            "max_attempts": 2,
        },
        blocking=True,
        context=context,
    )
    repeated = asyncio.Event()

    async def capture(call):
        calls.append(call)
        repeated.set()

    hass.services.async_register("notify", PHONE.notify_service, capture)
    unrelated = Context(user_id="another-user")
    await hass.services.async_call(
        DOMAIN,
        "set_ringer_mode",
        {"device_id": "phone", "mode": "normal"},
        blocking=True,
        context=unrelated,
    )
    repeated.clear()
    await asyncio.wait_for(repeated.wait(), 5)
    sounds = [call for call in calls if call.data["message"] == "Finding phone"]
    assert len(sounds) == 2
    assert all(call.context is context for call in sounds)
    assert (
        next(
            call for call in calls if call.data["message"] == "command_ringer_mode"
        ).context
        is unrelated
    )


async def test_prompt_survives_real_storage_and_entry_reload(server, tmp_path):
    hass, calls = server
    config_entry = entry()
    await async_setup_entry(hass, config_entry)
    response = await hass.services.async_call(
        DOMAIN,
        "ask_yes_no",
        {"device_id": "phone", "title": "Private title", "message": "Private message"},
        blocking=True,
        return_response=True,
    )
    token = calls[-1].data["data"]["actions"][0]["action"]
    saved = json.loads((tmp_path / ".storage" / STORE_KEY).read_text(encoding="utf-8"))
    assert "Private title" not in json.dumps(saved)
    assert "Private message" not in json.dumps(saved)
    await async_unload_entry(hass, config_entry)

    await async_setup_entry(hass, entry())
    manager = hass.data[DOMAIN][DATA_NOTIFICATION_MANAGER]
    await manager._async_ensure_loaded()
    events = []
    unsubscribe = hass.bus.async_listen(EVENT_NOTIFICATION_ACTION, events.append)
    context = Context()
    hass.bus.async_fire(
        EVENT_MOBILE_APP_NOTIFICATION_ACTION, {"action": token}, context=context
    )
    await asyncio.wait_for(hass.async_block_till_done(), 1)
    unsubscribe()
    assert len(events) == 1
    assert events[0].data["session_id"] == response["devices"][0]["session_id"]
    assert events[0].data["action_id"] == "yes"
    assert events[0].data["device_id"] == PHONE.device_id
    assert events[0].context is context
