"""Action registration and dispatch for Android Device Control."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .commands import (
    BLE_SETTINGS,
    DND_MODES,
    HIGH_ACCURACY_MODES,
    MEDIA_COMMANDS,
    PERSISTENT_MODES,
    RINGER_MODES,
    VOLUME_STREAMS,
    ble_configuration_payload,
    intent_payload,
    payload,
    raw_payload,
    screen_timeout_payload,
    toggle_payload,
)
from .const import *
from .device import AndroidTarget, resolve_android_targets

_LOGGER = logging.getLogger(__name__)

PayloadBuilder = Callable[[dict[str, Any]], dict[str, Any]]


def _device_ids(value: Any) -> list[str]:
    """Normalize one or more device IDs."""
    values = cv.ensure_list(value)
    if not values:
        raise vol.Invalid("At least one device is required")
    return [cv.string(item) for item in values]


BASE = {vol.Required(ATTR_DEVICE_ID): _device_ids}


def _schema(fields: dict[Any, Any] | None = None) -> vol.Schema:
    return vol.Schema(BASE | (fields or {}), extra=vol.PREVENT_EXTRA)


def _non_empty(value: Any) -> str:
    result = cv.string(value).strip()
    if not result:
        raise vol.Invalid("Value must not be empty")
    return result


def _app_lock_data(data: dict[str, Any]) -> dict[str, Any]:
    keys = ("app_lock_enabled", "app_lock_timeout", "home_bypass_enabled")
    result = {key: data[key] for key in keys if key in data}
    if not result:
        raise vol.Invalid("Set at least one app lock option")
    return result


def _ble_builder(data: dict[str, Any]) -> dict[str, Any]:
    return ble_configuration_payload(data["setting"], data["value"])


def _high_accuracy_builder(data: dict[str, Any]) -> dict[str, Any]:
    return payload("command_high_accuracy_mode", {"command": data["mode"]})


def _high_accuracy_interval_builder(data: dict[str, Any]) -> dict[str, Any]:
    return payload(
        "command_high_accuracy_mode",
        {
            "command": "high_accuracy_set_update_interval",
            "high_accuracy_update_interval": data["interval"],
        },
    )


def _activity_builder(data: dict[str, Any]) -> dict[str, Any]:
    return intent_payload("command_activity", data)


def _broadcast_builder(data: dict[str, Any]) -> dict[str, Any]:
    return intent_payload("command_broadcast_intent", data)


async def _async_send(
    hass: HomeAssistant, target: AndroidTarget, notify_payload: dict[str, Any]
) -> None:
    """Dispatch one already validated command."""
    _LOGGER.debug(
        "Dispatching %s to Android device %s via notify.%s",
        notify_payload["message"],
        target.device_id,
        target.notify_service,
    )
    service_data = dict(notify_payload)
    service_data["target"] = [target.webhook_id]
    await hass.services.async_call(
        "notify", target.notify_service, service_data, blocking=True
    )


async def _async_handle(
    hass: HomeAssistant, builder: PayloadBuilder, call: ServiceCall
) -> None:
    """Validate all devices, build once, and dispatch independently."""
    data = dict(call.data)
    device_ids = data.pop(ATTR_DEVICE_ID)
    targets = resolve_android_targets(hass, device_ids)
    try:
        notify_payload = builder(data)
    except vol.Invalid as err:
        raise ServiceValidationError(str(err)) from err

    results = await asyncio.gather(
        *(_async_send(hass, target, notify_payload) for target in targets),
        return_exceptions=True,
    )
    failures = [
        target.device_name
        for target, result in zip(targets, results, strict=True)
        if isinstance(result, BaseException)
    ]
    if failures:
        _LOGGER.warning(
            "Android command %s failed for %s",
            notify_payload["message"],
            ", ".join(failures),
        )
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="dispatch_failed",
            translation_placeholders={"devices": ", ".join(failures)},
        )


def _register(
    hass: HomeAssistant,
    name: str,
    schema: vol.Schema,
    builder: PayloadBuilder,
) -> None:
    hass.services.async_register(
        DOMAIN,
        name,
        partial(_async_handle, hass, builder),
        schema=schema,
    )


def async_register_services(hass: HomeAssistant) -> None:
    """Register all friendly Android command actions."""
    _register(
        hass,
        SERVICE_SET_RINGER_MODE,
        _schema({vol.Required("mode"): vol.In(RINGER_MODES)}),
        lambda data: payload("command_ringer_mode", {"command": data["mode"]}),
    )
    _register(
        hass,
        SERVICE_SET_VOLUME,
        _schema(
            {
                vol.Required("stream"): vol.In(VOLUME_STREAMS),
                vol.Required("level"): vol.All(vol.Coerce(int), vol.Range(min=0)),
            }
        ),
        lambda data: payload(
            "command_volume_level",
            {
                "media_stream": VOLUME_STREAMS[data["stream"]],
                "command": data["level"],
            },
        ),
    )
    _register(
        hass,
        SERVICE_SET_DO_NOT_DISTURB,
        _schema({vol.Required("mode"): vol.In(DND_MODES)}),
        lambda data: payload("command_dnd", {"command": data["mode"]}),
    )
    _register(
        hass,
        SERVICE_MEDIA_CONTROL,
        _schema(
            {
                vol.Required("media_command"): vol.In(MEDIA_COMMANDS),
                vol.Required("package_name"): _non_empty,
            }
        ),
        lambda data: payload(
            "command_media",
            {
                "media_command": data["media_command"],
                "media_package_name": data["package_name"],
            },
        ),
    )
    simple_messages = {
        SERVICE_STOP_TTS: "command_stop_tts",
        SERVICE_REQUEST_LOCATION_UPDATE: "request_location_update",
        SERVICE_UPDATE_SENSORS: "command_update_sensors",
        SERVICE_KIOSK_SHOW_SCREENSAVER: "kiosk_show_screensaver",
        SERVICE_KIOSK_HIDE_SCREENSAVER: "kiosk_hide_screensaver",
        SERVICE_KIOSK_HIDE_CAMERA: "kiosk_hide_camera",
        SERVICE_KIOSK_RELOAD: "kiosk_reload",
        SERVICE_KIOSK_DEFAULT: "kiosk_default",
    }
    for service_name, message in simple_messages.items():
        _register(
            hass, service_name, _schema(), lambda _data, msg=message: payload(msg)
        )

    _register(
        hass,
        SERVICE_TURN_SCREEN_ON,
        _schema({vol.Optional("keep_screen_on", default=False): cv.boolean}),
        lambda data: payload(
            "command_screen_on",
            {"command": "keep_screen_on" if data["keep_screen_on"] else "reset"},
        ),
    )
    _register(
        hass,
        SERVICE_SET_SCREEN_BRIGHTNESS,
        _schema(
            {vol.Required("level"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255))}
        ),
        lambda data: payload(
            "command_screen_brightness_level", {"command": data["level"]}
        ),
    )
    _register(
        hass,
        SERVICE_SET_SCREEN_TIMEOUT,
        _schema({vol.Required("duration"): cv.positive_time_period_dict}),
        lambda data: screen_timeout_payload(data["duration"]),
    )
    for name, message in {
        SERVICE_SET_AUTO_BRIGHTNESS: "command_auto_screen_brightness",
        SERVICE_SET_FLASHLIGHT: "command_flashlight",
        SERVICE_SET_BLUETOOTH: "command_bluetooth",
        SERVICE_SET_BLE_TRANSMITTER: "command_ble_transmitter",
        SERVICE_SET_BEACON_MONITOR: "command_beacon_monitor",
        SERVICE_SET_WAKE_WORD_DETECTION: "command_wake_word_detection",
    }.items():
        _register(
            hass,
            name,
            _schema({vol.Required("enabled"): cv.boolean}),
            lambda data, msg=message: toggle_payload(msg, data["enabled"]),
        )

    _register(
        hass,
        SERVICE_OPEN_WEBVIEW,
        _schema({vol.Optional("path", default=""): cv.string}),
        lambda data: payload("command_webview", {"command": data["path"]}),
    )
    _register(
        hass,
        SERVICE_CONFIGURE_BLE_TRANSMITTER,
        _schema(
            {
                vol.Required("setting"): vol.In(BLE_SETTINGS),
                vol.Required("value"): vol.Any(str, int),
            }
        ),
        _ble_builder,
    )
    _register(
        hass,
        SERVICE_SET_HIGH_ACCURACY_MODE,
        _schema({vol.Required("mode"): vol.In(HIGH_ACCURACY_MODES)}),
        _high_accuracy_builder,
    )
    _register(
        hass,
        SERVICE_SET_HIGH_ACCURACY_INTERVAL,
        _schema({vol.Required("interval"): vol.All(vol.Coerce(int), vol.Range(min=5))}),
        _high_accuracy_interval_builder,
    )
    _register(
        hass,
        SERVICE_LAUNCH_APP,
        _schema({vol.Required("package_name"): _non_empty}),
        lambda data: payload(
            "command_launch_app", {"package_name": data["package_name"]}
        ),
    )
    intent_fields = {
        vol.Required("intent_action"): _non_empty,
        vol.Optional("package_name"): cv.string,
        vol.Optional("class_name"): cv.string,
        vol.Optional("uri"): cv.string,
        vol.Optional("mime_type"): cv.string,
        vol.Optional("extras"): cv.string,
    }
    _register(hass, SERVICE_LAUNCH_ACTIVITY, _schema(intent_fields), _activity_builder)
    _register(
        hass,
        SERVICE_SEND_BROADCAST_INTENT,
        _schema(intent_fields),
        _broadcast_builder,
    )
    _register(
        hass,
        SERVICE_SET_APP_LOCK,
        _schema(
            {
                vol.Optional("app_lock_enabled"): cv.boolean,
                vol.Optional("app_lock_timeout"): vol.All(
                    vol.Coerce(int), vol.Range(min=0)
                ),
                vol.Optional("home_bypass_enabled"): cv.boolean,
            }
        ),
        lambda data: payload("command_app_lock", _app_lock_data(data)),
    )
    _register(
        hass,
        SERVICE_SET_PERSISTENT_CONNECTION,
        _schema({vol.Required("mode"): vol.In(PERSISTENT_MODES)}),
        lambda data: payload(
            "command_persistent_connection", {"persistent": data["mode"]}
        ),
    )
    _register(
        hass,
        SERVICE_CLEAR_NOTIFICATION,
        _schema({vol.Required("tag"): _non_empty}),
        lambda data: payload("clear_notification", {"tag": data["tag"]}),
    )
    _register(
        hass,
        SERVICE_REMOVE_NOTIFICATION_CHANNEL,
        _schema({vol.Required("channel"): _non_empty}),
        lambda data: payload("remove_channel", {"channel": data["channel"]}),
    )
    _register(
        hass,
        SERVICE_KIOSK_SHOW_CAMERA,
        _schema({vol.Required("entity_id"): cv.entity_id}),
        lambda data: payload("kiosk_show_camera", {"entity_id": data["entity_id"]}),
    )
    for name, message, field in (
        (SERVICE_KIOSK_SET_BRIGHTNESS, "kiosk_set_brightness", "level"),
        (SERVICE_KIOSK_SET_VOLUME, "kiosk_set_volume", "volume"),
    ):
        _register(
            hass,
            name,
            _schema(
                {
                    vol.Required(field): vol.All(
                        vol.Coerce(float), vol.Range(min=0, max=100)
                    )
                }
            ),
            lambda data, msg=message, key=field: payload(msg, {key: data[key]}),
        )
    _register(
        hass,
        SERVICE_SEND_COMMAND,
        _schema(
            {
                vol.Required("command"): _non_empty,
                vol.Optional("data", default={}): dict,
            }
        ),
        lambda data: raw_payload(data["command"], data["data"]),
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister integration actions."""
    for service in list(hass.services.async_services().get(DOMAIN, {})):
        hass.services.async_remove(DOMAIN, service)
