"""Tests for Android Mobile App target resolution."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.android_device_control import device as device_module

MOBILE_APP_DOMAIN = "mobile_app"
DATA_CONFIG_ENTRIES = "config_entries"


@pytest.fixture
def hass() -> SimpleNamespace:
    services = SimpleNamespace(has_service=Mock(return_value=True))
    return SimpleNamespace(
        data={
            MOBILE_APP_DOMAIN: {
                DATA_CONFIG_ENTRIES: {
                    "webhook-phone": SimpleNamespace(data={"os_name": "Android"})
                }
            }
        },
        services=services,
    )


@pytest.fixture(autouse=True)
def mock_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    phone = SimpleNamespace(id="phone", name="Pixel 9", name_by_user=None)
    registry = SimpleNamespace(
        async_get=lambda device_id: phone if device_id == "phone" else None
    )
    monkeypatch.setattr(device_module.dr, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        device_module,
        "webhook_id_from_device_id",
        lambda hass, device_id: "webhook-phone" if device_id == "phone" else None,
    )
    monkeypatch.setattr(device_module, "supports_push", lambda hass, webhook_id: True)
    monkeypatch.setattr(
        device_module,
        "get_notify_service",
        lambda hass, webhook_id: "mobile_app_pixel_9",
    )


def test_resolves_exact_webhook_notify_target(hass: SimpleNamespace) -> None:
    target = device_module.resolve_android_target(hass, "phone")
    assert target.device_id == "phone"
    assert target.webhook_id == "webhook-phone"
    assert target.notify_service == "mobile_app_pixel_9"


def test_rejects_unknown_device(hass: SimpleNamespace) -> None:
    with pytest.raises(ServiceValidationError):
        device_module.resolve_android_target(hass, "missing")


def test_rejects_non_mobile_app_device(
    hass: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        device_module, "webhook_id_from_device_id", lambda hass, device_id: None
    )
    with pytest.raises(ServiceValidationError):
        device_module.resolve_android_target(hass, "phone")


def test_rejects_ios_device(hass: SimpleNamespace) -> None:
    hass.data[MOBILE_APP_DOMAIN][DATA_CONFIG_ENTRIES]["webhook-phone"].data[
        "os_name"
    ] = "iOS"
    with pytest.raises(ServiceValidationError):
        device_module.resolve_android_target(hass, "phone")


def test_rejects_missing_notify_action(hass: SimpleNamespace) -> None:
    hass.services.has_service.return_value = False
    with pytest.raises(ServiceValidationError):
        device_module.resolve_android_target(hass, "phone")


def test_deduplicates_multiple_devices(
    hass: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = Mock(return_value=SimpleNamespace(device_id="phone"))
    monkeypatch.setattr(device_module, "resolve_android_target", resolver)
    assert len(device_module.resolve_android_targets(hass, ["phone", "phone"])) == 1
    resolver.assert_called_once_with(hass, "phone")
