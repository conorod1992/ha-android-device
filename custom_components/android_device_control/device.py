"""Resolve Home Assistant devices to Android Mobile App notify targets."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

MOBILE_APP_DOMAIN = "mobile_app"
DATA_CONFIG_ENTRIES = "config_entries"
ATTR_OS_NAME = "os_name"


def webhook_id_from_device_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Use Mobile App's current device-to-webhook resolver."""
    from homeassistant.components.mobile_app.util import (  # noqa: PLC0415
        webhook_id_from_device_id,
    )

    return webhook_id_from_device_id(hass, device_id)


def supports_push(hass: HomeAssistant, webhook_id: str) -> bool:
    """Use Mobile App's current push capability check."""
    from homeassistant.components.mobile_app.util import supports_push  # noqa: PLC0415

    return supports_push(hass, webhook_id)


def get_notify_service(hass: HomeAssistant, webhook_id: str) -> str | None:
    """Use Mobile App's exact webhook-to-notify mapping."""
    from homeassistant.components.mobile_app.util import (  # noqa: PLC0415
        get_notify_service,
    )

    return get_notify_service(hass, webhook_id)


@dataclass(frozen=True, slots=True)
class AndroidTarget:
    """A validated Android Mobile App notification target."""

    device_id: str
    device_name: str
    webhook_id: str
    notify_service: str


def _validation_error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders,
    )


def resolve_android_target(hass: HomeAssistant, device_id: str) -> AndroidTarget:
    """Resolve one device without relying on a generated notify service name."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise _validation_error("device_not_found", device_id=device_id)

    webhook_id = webhook_id_from_device_id(hass, device_id)
    if webhook_id is None:
        raise _validation_error(
            "not_mobile_app_device", device_name=device.name_by_user or device.name
        )

    entry = hass.data[MOBILE_APP_DOMAIN][DATA_CONFIG_ENTRIES].get(webhook_id)
    if entry is None:
        raise _validation_error(
            "registration_missing", device_name=device.name_by_user or device.name
        )

    os_name = str(entry.data.get(ATTR_OS_NAME, ""))
    if os_name.casefold() != "android":
        raise _validation_error(
            "not_android_device", device_name=device.name_by_user or device.name
        )
    if not supports_push(hass, webhook_id):
        raise _validation_error(
            "push_not_supported", device_name=device.name_by_user or device.name
        )

    notify_service = get_notify_service(hass, webhook_id)
    if notify_service is None or not hass.services.has_service(
        "notify", notify_service
    ):
        raise _validation_error(
            "notify_target_missing", device_name=device.name_by_user or device.name
        )

    return AndroidTarget(
        device_id=device_id,
        device_name=device.name_by_user or device.name,
        webhook_id=webhook_id,
        notify_service=notify_service,
    )


def resolve_android_targets(
    hass: HomeAssistant, device_ids: list[str]
) -> list[AndroidTarget]:
    """Resolve and validate all targets before any command is dispatched."""
    return [
        resolve_android_target(hass, device_id)
        for device_id in dict.fromkeys(device_ids)
    ]
