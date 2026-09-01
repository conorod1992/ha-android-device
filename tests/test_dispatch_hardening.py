"""Regression tests for dispatch visibility and generic BLE validation."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.android_device_control import services as services_module
from custom_components.android_device_control.const import DOMAIN
from custom_components.android_device_control.device import AndroidTarget


class FakeServices:
    """Minimal action registry with optional per-target transport failures."""

    def __init__(self) -> None:
        self.handlers = {}
        self.schemas = {}
        self.calls = []
        self.responses = {}
        self.fail_targets: set[str] = set()

    def async_register(
        self,
        domain,
        name,
        handler,
        schema=None,
        supports_response=SupportsResponse.NONE,
    ) -> None:
        self.handlers[name] = handler
        self.schemas[name] = schema
        self.responses[name] = supports_response

    async def async_call(self, domain, service, data, *, blocking) -> None:
        self.calls.append((domain, service, data, blocking))
        target = data["target"][0]
        if target in self.fail_targets:
            raise HomeAssistantError(f"transport failed for {target}")

    def async_services(self):
        return {DOMAIN: {name: {} for name in self.handlers}}

    def async_remove(self, domain, service) -> None:
        self.handlers.pop(service, None)
        self.schemas.pop(service, None)


class FakeBus:
    """Minimal event listener registry."""

    def __init__(self) -> None:
        self.listeners = []

    def async_listen(self, event_type, listener):
        item = (event_type, listener)
        self.listeners.append(item)
        return lambda: self.listeners.remove(item) if item in self.listeners else None

    def async_listen_once(self, event_type, listener):
        return self.async_listen(event_type, listener)


@pytest.fixture
async def hass(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Return a registered fake Home Assistant instance with one Android target."""
    instance = SimpleNamespace(services=FakeServices(), bus=FakeBus(), data={})
    instance.async_create_task = lambda coro, name: asyncio.create_task(coro, name=name)
    phone = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")
    monkeypatch.setattr(
        services_module,
        "resolve_android_targets",
        lambda _hass, _ids: [phone],
    )
    services_module.async_register_services(instance)
    yield instance
    await services_module.async_unregister_services(instance)


def _two_target_resolver():
    phone = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")
    tablet = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    targets = {phone.device_id: phone, tablet.device_id: tablet}
    return lambda _hass, ids: [targets[ids[0]]]


async def _call(hass: SimpleNamespace, service: str, data: dict) -> None:
    validated = hass.services.schemas[service](data)
    await hass.services.handlers[service](
        ServiceCall(hass, DOMAIN, service, validated)
    )


async def test_generic_ble_bad_measured_power_is_service_validation_error(
    hass: SimpleNamespace,
) -> None:
    validated = hass.services.schemas["configure_ble_transmitter"](
        {
            "device_id": "phone",
            "setting": "measured_power",
            "value": "not-a-number",
        }
    )

    with pytest.raises(ServiceValidationError, match="negative number"):
        await hass.services.handlers["configure_ble_transmitter"](
            ServiceCall(hass, DOMAIN, "configure_ble_transmitter", validated)
        )

    assert hass.services.calls == []


async def test_partial_resolution_failure_is_logged_without_blocking_valid_target(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    hass: SimpleNamespace,
) -> None:
    phone = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")

    def resolve(_hass, ids):
        if ids == ["stale"]:
            raise HomeAssistantError("stale target")
        return [phone]

    monkeypatch.setattr(services_module, "resolve_android_targets", resolve)

    with caplog.at_level(logging.WARNING, logger=services_module.__name__):
        await _call(hass, "stop_tts", {"device_id": ["stale", "phone"]})

    assert [call[2]["target"] for call in hass.services.calls] == [["webhook-phone"]]
    assert (
        "Android command command_stop_tts succeeded for at least one device"
        in caplog.text
    )
    assert "stale: stale target" in caplog.text


async def test_partial_transport_failure_is_logged_without_blocking_other_target(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    hass: SimpleNamespace,
) -> None:
    monkeypatch.setattr(
        services_module, "resolve_android_targets", _two_target_resolver()
    )
    hass.services.fail_targets.add("webhook-tablet")

    with caplog.at_level(logging.WARNING, logger=services_module.__name__):
        await _call(hass, "stop_tts", {"device_id": ["phone", "tablet"]})

    assert [call[2]["target"] for call in hass.services.calls] == [
        ["webhook-phone"],
        ["webhook-tablet"],
    ]
    assert (
        "Android command command_stop_tts succeeded for at least one device"
        in caplog.text
    )
    assert "Pixel Tablet: transport failed for webhook-tablet" in caplog.text


async def test_find_phone_partial_failure_is_logged_and_valid_session_survives(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    hass: SimpleNamespace,
) -> None:
    monkeypatch.setattr(
        services_module, "resolve_android_targets", _two_target_resolver()
    )
    hass.services.fail_targets.add("webhook-tablet")

    with caplog.at_level(logging.WARNING, logger=services_module.__name__):
        await _call(
            hass,
            "find_phone",
            {
                "device_id": ["phone", "tablet"],
                "wake_screen": False,
                "repeat": False,
            },
        )

    manager = hass.data[DOMAIN][services_module.DATA_FIND_PHONE_MANAGER]
    assert set(manager.sessions) == {"phone"}
    assert "Find Phone succeeded for at least one device" in caplog.text
    assert "Pixel Tablet: Android Device Control could not dispatch to Pixel Tablet" in caplog.text


async def test_stop_find_phone_logs_partial_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    hass: SimpleNamespace,
) -> None:
    phone = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")

    def resolve(_hass, ids):
        if ids == ["stale"]:
            raise HomeAssistantError("stale target")
        return [phone]

    monkeypatch.setattr(services_module, "resolve_android_targets", resolve)

    with caplog.at_level(logging.WARNING, logger=services_module.__name__):
        await _call(
            hass,
            "stop_find_phone",
            {"device_id": ["stale", "phone"]},
        )

    assert "Stop Find Phone succeeded for at least one device" in caplog.text
    assert "stale: stale target" in caplog.text
