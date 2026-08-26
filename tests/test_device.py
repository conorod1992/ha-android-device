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
    phone = SimpleNamespace(id="phone", name="Pixel 9", name_by_user=None)
    services = SimpleNamespace(has_service=Mock(return_value=True))
    return SimpleNamespace(
        data={
            MOBILE_APP_DOMAIN: {
                DATA_CONFIG_ENTRIES: {
                    "webhook-phone": SimpleNamespace(
                        data={
                            "os_name": "Android",
                            "os_version": "14",
                            "app_name": "Home Assistant",
                            "app_version": "2026.7.1-full",
                        }
                    )
                },
                "devices": {"webhook-phone": phone},
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


@pytest.mark.parametrize("mobile_data", [None, {}, {DATA_CONFIG_ENTRIES: None}])
def test_missing_mobile_app_runtime_state_is_controlled(
    hass: SimpleNamespace, mobile_data
) -> None:
    if mobile_data is None:
        hass.data.pop(MOBILE_APP_DOMAIN)
    else:
        hass.data[MOBILE_APP_DOMAIN] = mobile_data

    with pytest.raises(ServiceValidationError) as error:
        device_module.resolve_android_target(hass, "phone")

    assert error.value.translation_key == "mobile_app_unavailable"


def test_missing_mobile_app_compatibility_helper_is_controlled(
    hass: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        device_module,
        "webhook_id_from_device_id",
        lambda *_args: (_ for _ in ()).throw(ImportError("helper moved")),
    )

    with pytest.raises(ServiceValidationError) as error:
        device_module.resolve_android_target(hass, "phone")

    assert error.value.translation_key == "mobile_app_unavailable"


def test_deduplicates_multiple_devices(
    hass: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = Mock(return_value=SimpleNamespace(device_id="phone"))
    monkeypatch.setattr(device_module, "resolve_android_target", resolver)
    assert len(device_module.resolve_android_targets(hass, ["phone", "phone"])) == 1
    resolver.assert_called_once_with(hass, "phone")


def test_inspects_ready_android_device(hass: SimpleNamespace) -> None:
    result = device_module.inspect_mobile_app_device(hass, "phone")
    assert result["status"] == "ready"
    assert result["ready"] is True
    assert result["verified"] == {
        "device_exists": True,
        "mobile_app_device": True,
        "registration_exists": True,
        "push_supported": True,
        "notify_target_available": True,
    }
    assert result["metadata"] == {
        "os_name": "Android",
        "os_version": "14",
        "is_android": True,
        "app_name": "Home Assistant",
        "app_version": "2026.7.1-full",
        "notify_service": "notify.mobile_app_pixel_9",
    }
    assert "Android 13 and newer" in result["compatibility_observations"][0]
    assert result["execution"]["guaranteed"] is False


def test_inspection_returns_structured_result_for_missing_device(
    hass: SimpleNamespace,
) -> None:
    result = device_module.inspect_mobile_app_device(hass, "missing")
    assert result["status"] == "device_not_found"
    assert result["ready"] is False
    assert result["verified"]["device_exists"] is False


def test_inspection_reports_missing_push(
    hass: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(device_module, "supports_push", lambda _hass, _id: False)
    result = device_module.inspect_mobile_app_device(hass, "phone")
    assert result["status"] == "push_unavailable"
    assert result["verified"]["registration_exists"] is True
    assert result["verified"]["push_supported"] is False
    assert "push channel" in result["compatibility_observations"][-1]


def test_discovers_registered_android_devices(hass: SimpleNamespace) -> None:
    devices = device_module.discover_android_devices(hass)
    assert [(item["device_id"], item["device_name"]) for item in devices] == [
        ("phone", "Pixel 9")
    ]
