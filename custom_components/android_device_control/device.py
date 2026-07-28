"""Resolve Home Assistant devices to Android Mobile App notify targets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

MOBILE_APP_DOMAIN = "mobile_app"
DATA_CONFIG_ENTRIES = "config_entries"
ATTR_OS_NAME = "os_name"
ATTR_OS_VERSION = "os_version"
ATTR_APP_NAME = "app_name"
ATTR_APP_VERSION = "app_version"
DATA_DEVICES = "devices"
ANDROID_BLUETOOTH_TOGGLE_MAX_VERSION = 12
ANDROID_ASSISTANT_STREAM_MIN_VERSION = 17


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


def _device_name(device: Any, device_id: str) -> str:
    """Return the best available friendly name for a registry device."""
    if device is None:
        return device_id
    return device.name_by_user or device.name or device_id


def _compatibility_observations(
    *,
    is_android: bool,
    os_version: str | None,
    registration_exists: bool,
    push_supported: bool,
    notify_target_available: bool,
) -> list[str]:
    """Build metadata-based observations without implying runtime verification."""
    observations: list[str] = []
    match = re.match(r"\d+", os_version) if is_android and os_version else None
    android_version = int(match.group()) if match else None
    if (
        android_version is not None
        and android_version > ANDROID_BLUETOOTH_TOGGLE_MAX_VERSION
    ):
        observations.append(
            "Android 13 and newer do not allow direct Bluetooth toggling."
        )
    if (
        android_version is not None
        and android_version < ANDROID_ASSISTANT_STREAM_MIN_VERSION
    ):
        observations.append(
            "The assistant volume stream is only available on Android 17 and newer."
        )
    if registration_exists and not push_supported:
        observations.append(
            "This Mobile App registration does not report an available push channel."
        )
    if push_supported and not notify_target_available:
        observations.append(
            "Push is registered, but its Mobile App notify target is not currently "
            "available."
        )
    return observations


def _compatibility_status(
    *,
    device_exists: bool,
    mobile_app_device: bool,
    registration_exists: bool,
    is_android: bool,
    push_supported: bool,
) -> str:
    """Summarize the first incompatibility Home Assistant can verify."""
    if not device_exists:
        return "device_not_found"
    if not mobile_app_device:
        return "not_mobile_app"
    if not registration_exists:
        return "registration_missing"
    if not is_android:
        return "not_android"
    if not push_supported:
        return "push_unavailable"
    return "notify_target_unavailable"


def inspect_mobile_app_device(hass: HomeAssistant, device_id: str) -> dict[str, Any]:
    """Return facts Home Assistant can verify about a possible Android target."""
    registry_device = dr.async_get(hass).async_get(device_id)
    device_exists = registry_device is not None
    name = _device_name(registry_device, device_id)
    webhook_id = webhook_id_from_device_id(hass, device_id) if device_exists else None
    mobile_app_device = webhook_id is not None
    mobile_data = hass.data.get(MOBILE_APP_DOMAIN, {})
    entries = mobile_data.get(DATA_CONFIG_ENTRIES, {})
    entry = entries.get(webhook_id) if webhook_id else None
    registration_exists = entry is not None
    entry_data = entry.data if entry else {}
    os_name = str(entry_data.get(ATTR_OS_NAME, "")) or None
    os_version = str(entry_data.get(ATTR_OS_VERSION, "")) or None
    is_android = bool(os_name and os_name.casefold() == "android")
    push_supported = bool(
        webhook_id and registration_exists and supports_push(hass, webhook_id)
    )
    notify_service = get_notify_service(hass, webhook_id) if webhook_id else None
    notify_target_available = bool(
        notify_service and hass.services.has_service("notify", notify_service)
    )
    ready = bool(
        device_exists
        and mobile_app_device
        and registration_exists
        and is_android
        and push_supported
        and notify_target_available
    )

    observations = _compatibility_observations(
        is_android=is_android,
        os_version=os_version,
        registration_exists=registration_exists,
        push_supported=push_supported,
        notify_target_available=notify_target_available,
    )
    status = (
        "ready"
        if ready
        else _compatibility_status(
            device_exists=device_exists,
            mobile_app_device=mobile_app_device,
            registration_exists=registration_exists,
            is_android=is_android,
            push_supported=push_supported,
        )
    )

    return {
        "device_id": device_id,
        "device_name": name,
        "status": status,
        "ready": ready,
        "verified": {
            "device_exists": device_exists,
            "mobile_app_device": mobile_app_device,
            "registration_exists": registration_exists,
            "push_supported": push_supported,
            "notify_target_available": notify_target_available,
        },
        "metadata": {
            "os_name": os_name,
            "os_version": os_version,
            "is_android": is_android,
            "app_name": entry_data.get(ATTR_APP_NAME),
            "app_version": entry_data.get(ATTR_APP_VERSION),
            "notify_service": (
                f"notify.{notify_service}" if notify_service is not None else None
            ),
        },
        "compatibility_observations": observations,
        "execution": {
            "guaranteed": False,
            "note": (
                "Home Assistant can verify registration and delivery metadata only. "
                "Android permissions, device settings, and command execution are "
                "determined on the device."
            ),
        },
    }


def discover_android_devices(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Discover current Android Mobile App registrations without contacting them."""
    mobile_data = hass.data.get(MOBILE_APP_DOMAIN, {})
    registrations = mobile_data.get(DATA_CONFIG_ENTRIES, {})
    devices = mobile_data.get(DATA_DEVICES, {})
    discovered = []
    for webhook_id, registry_device in devices.items():
        entry = registrations.get(webhook_id)
        if (
            entry is None
            or str(entry.data.get(ATTR_OS_NAME, "")).casefold() != "android"
        ):
            continue
        discovered.append(inspect_mobile_app_device(hass, registry_device.id))
    return sorted(discovered, key=lambda item: item["device_name"].casefold())


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
