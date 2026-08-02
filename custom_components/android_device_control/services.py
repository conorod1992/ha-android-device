"""Action registration and dispatch for Android Device Control."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import timedelta
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .apps import packages_for, resolve_app
from .commands import (
    ALARM_SEARCH_MODES,
    BLE_SETTINGS,
    COMMON_APPS,
    DND_MODES,
    HIGH_ACCURACY_MODES,
    MEDIA_COMMANDS,
    PERSISTENT_MODES,
    RINGER_MODES,
    TTS_PLAYBACK_MODES,
    VOLUME_STREAMS,
    WEEKDAY_NUMBERS,
    ble_configuration_payload,
    dismiss_alarm_payload,
    dismiss_expired_timers_payload,
    intent_payload,
    payload,
    raw_payload,
    resolve_launch_package,
    screen_timeout_payload,
    set_alarm_payload,
    set_timer_payload,
    show_alarms_payload,
    show_timers_payload,
    snooze_alarm_payload,
    toggle_payload,
    tts_payload,
)
from .const import *
from .device import AndroidTarget, inspect_mobile_app_device, resolve_android_targets
from .find_phone import (
    FindPhoneOptions,
    async_remove_find_phone_manager,
    get_find_phone_manager,
)
from .intents import (
    APP_SETTINGS,
    SETTINGS,
    app_settings_payload,
    calendar_payload,
    camera_payload,
    dial_payload,
    email_payload,
    navigate_payload,
    open_url_payload,
    settings_payload,
    show_map_payload,
    sms_payload,
    web_search_payload,
)

_LOGGER = logging.getLogger(__name__)

PayloadBuilder = Callable[[dict[str, Any]], dict[str, Any]]
PACKAGE_ID_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)


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


def _find_phone_repeat_interval(value: Any) -> timedelta:
    """Validate and normalize the delay between Find Phone attempts."""
    duration = cv.positive_time_period_dict(value)
    seconds = duration.total_seconds()
    if not MIN_FIND_PHONE_REPEAT_INTERVAL <= seconds <= MAX_FIND_PHONE_REPEAT_INTERVAL:
        raise vol.Invalid("Repeat interval must be between 3 seconds and 10 minutes")
    return duration


def _package_id(value: Any) -> str:
    """Validate an Android application package identifier."""
    result = _non_empty(value)
    if PACKAGE_ID_PATTERN.fullmatch(result) is None:
        raise vol.Invalid("Enter a valid Android package ID, such as com.example.app")
    return result


def _clock_fields() -> dict[Any, Any]:
    """Return the shared optional clock-application fields."""
    return {
        vol.Optional("clock_app", default="default"): vol.In(
            {"default", "google_clock", "custom"}
        ),
        vol.Optional("clock_package"): _package_id,
    }


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


def _media_builder(data: dict[str, Any]) -> dict[str, Any]:
    return payload(
        "command_media",
        {
            "media_command": data["media_command"],
            "media_package_name": resolve_app(data, capability="media"),
        },
    )


def _screen_brightness_builder(data: dict[str, Any]) -> dict[str, Any]:
    """Convert friendly whole percentages, retaining the old raw YAML field."""
    if "brightness" in data:
        percentage = data["brightness"]
        # Integer half-up rounding keeps both endpoints exact and avoids float drift.
        raw_level = (percentage * 255 + 50) // 100
    elif "level" in data:
        raw_level = data["level"]
    else:
        raise vol.Invalid("Brightness is required")
    return payload("command_screen_brightness_level", {"command": raw_level})


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


async def _async_find_phone(hass: HomeAssistant, call: ServiceCall) -> None:
    """Start independent, bounded Find Phone sessions for selected devices."""
    data = dict(call.data)
    targets = resolve_android_targets(hass, data.pop(ATTR_DEVICE_ID))
    options = FindPhoneOptions(
        wake_screen=data["wake_screen"],
        flashlight=data["flashlight"],
        sound_mode=data["sound_mode"],
        message=data["message"],
        repeat=data["repeat"],
        max_attempts=data["max_attempts"],
        repeat_interval=data["repeat_interval"].total_seconds(),
        show_stop_action=data["show_stop_action"],
    )
    manager = get_find_phone_manager(hass, partial(_async_send, hass))
    results = await asyncio.gather(
        *(manager.async_start(target, options) for target in targets),
        return_exceptions=True,
    )
    failures = [
        target.device_name
        for target, result in zip(targets, results, strict=True)
        if isinstance(result, BaseException)
    ]
    if failures:
        _LOGGER.warning("Find Phone could not start for %s", ", ".join(failures))
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="dispatch_failed",
            translation_placeholders={"devices": ", ".join(failures)},
        )


async def _async_stop_find_phone(hass: HomeAssistant, call: ServiceCall) -> None:
    """Stop selected sessions and perform best-effort phone-side cleanup."""
    data = dict(call.data)
    targets = resolve_android_targets(hass, data.pop(ATTR_DEVICE_ID))
    manager = get_find_phone_manager(hass, partial(_async_send, hass))
    await asyncio.gather(
        *(
            manager.async_stop(
                target,
                turn_off_flashlight=data["turn_off_flashlight"],
            )
            for target in targets
        )
    )


async def _async_check_device(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    """Return compatibility facts without sending anything to the device."""
    return inspect_mobile_app_device(hass, call.data[ATTR_DEVICE_ID])


def _register_find_phone_services(hass: HomeAssistant) -> None:
    """Register Find Phone actions and initialize their event manager."""
    get_find_phone_manager(hass, partial(_async_send, hass))
    hass.services.async_register(
        DOMAIN,
        SERVICE_FIND_PHONE,
        partial(_async_find_phone, hass),
        schema=_schema(
            {
                vol.Optional("wake_screen", default=True): cv.boolean,
                vol.Optional("flashlight", default=False): cv.boolean,
                vol.Optional("sound_mode", default="ringtone"): vol.In(
                    {"ringtone", "tts"}
                ),
                vol.Optional("message", default="Finding phone"): _non_empty,
                vol.Optional("repeat", default=True): cv.boolean,
                vol.Optional(
                    "max_attempts", default=DEFAULT_FIND_PHONE_MAX_ATTEMPTS
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_FIND_PHONE_ATTEMPTS,
                        max=MAX_FIND_PHONE_ATTEMPTS,
                    ),
                ),
                vol.Optional(
                    "repeat_interval",
                    default={"seconds": DEFAULT_FIND_PHONE_REPEAT_INTERVAL},
                ): _find_phone_repeat_interval,
                vol.Optional("show_stop_action", default=True): cv.boolean,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_FIND_PHONE,
        partial(_async_stop_find_phone, hass),
        schema=_schema(
            {vol.Optional("turn_off_flashlight", default=False): cv.boolean}
        ),
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
                vol.Optional("app"): vol.In(packages_for("media") | {"custom"}),
                vol.Optional("package_name"): _package_id,
            }
        ),
        _media_builder,
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
        vol.Any(
            _schema(
                {
                    vol.Required("brightness"): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=100)
                    )
                }
            ),
            _schema(
                {
                    vol.Required("level"): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=255)
                    )
                }
            ),
        ),
        _screen_brightness_builder,
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
        SERVICE_SET_ALARM,
        _schema(
            {
                vol.Required("alarm_time"): cv.time,
                vol.Optional("label"): cv.string,
                vol.Optional("repeat"): vol.All(
                    cv.ensure_list, [vol.In(WEEKDAY_NUMBERS)]
                ),
                vol.Optional("vibrate"): cv.boolean,
                vol.Optional("ringtone", default="default"): vol.In(
                    {"default", "silent", "custom"}
                ),
                vol.Optional("ringtone_uri"): cv.string,
                vol.Optional("skip_ui", default=False): cv.boolean,
            }
            | _clock_fields()
        ),
        set_alarm_payload,
    )
    _register(
        hass,
        SERVICE_DISMISS_ALARM,
        _schema(
            {
                vol.Required("alarm"): vol.In(ALARM_SEARCH_MODES),
                vol.Optional("alarm_time"): cv.time,
                vol.Optional("alarm_label"): cv.string,
            }
            | _clock_fields()
        ),
        dismiss_alarm_payload,
    )
    _register(
        hass,
        SERVICE_SNOOZE_ALARM,
        _schema(
            {vol.Optional("duration"): cv.positive_time_period_dict} | _clock_fields()
        ),
        snooze_alarm_payload,
    )
    _register(
        hass,
        SERVICE_SHOW_ALARMS,
        _schema(_clock_fields()),
        show_alarms_payload,
    )
    _register(
        hass,
        SERVICE_SET_TIMER,
        _schema(
            {
                vol.Required("duration"): cv.positive_time_period_dict,
                vol.Optional("label"): cv.string,
                vol.Optional("skip_ui", default=False): cv.boolean,
            }
            | _clock_fields()
        ),
        set_timer_payload,
    )
    _register(
        hass,
        SERVICE_DISMISS_EXPIRED_TIMERS,
        _schema(_clock_fields()),
        dismiss_expired_timers_payload,
    )
    _register(
        hass,
        SERVICE_SHOW_TIMERS,
        _schema(_clock_fields()),
        show_timers_payload,
    )
    _register(
        hass,
        SERVICE_LAUNCH_APP,
        _schema(
            {
                vol.Optional("app"): vol.In(set(COMMON_APPS) | {"custom"}),
                vol.Optional("package_name"): _package_id,
            }
        ),
        lambda data: payload(
            "command_launch_app", {"package_name": resolve_launch_package(data)}
        ),
    )
    intent_fields = {
        vol.Required("intent_action"): _non_empty,
        vol.Optional("package_name"): cv.string,
        vol.Optional("class_name"): cv.string,
        vol.Optional("uri"): cv.string,
        vol.Optional("mime_type"): cv.string,
        vol.Optional("extras"): cv.string,
        vol.Optional("structured_extras", default=[]): [dict],
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

    _register(
        hass,
        SERVICE_OPEN_URL,
        _schema(
            {
                vol.Required("url"): _non_empty,
                vol.Optional("app"): vol.In(packages_for("browser") | {"custom"}),
                vol.Optional("package_name"): _package_id,
            }
        ),
        open_url_payload,
    )
    location_fields = {
        vol.Optional("location"): cv.string,
        vol.Optional("latitude"): vol.Coerce(float),
        vol.Optional("longitude"): vol.Coerce(float),
        vol.Optional("label"): cv.string,
        vol.Optional("provider", default="default"): vol.In(
            {"default", "google_maps", "waze"}
        ),
    }
    _register(hass, SERVICE_SHOW_MAP, _schema(location_fields), show_map_payload)
    _register(
        hass,
        SERVICE_NAVIGATE_TO,
        _schema(
            location_fields
            | {
                vol.Optional("travel_mode", default="default"): vol.In(
                    {"default", "driving", "walking", "bicycling", "transit"}
                )
            }
        ),
        navigate_payload,
    )
    _register(
        hass,
        SERVICE_DIAL_NUMBER,
        _schema({vol.Required("phone_number"): _non_empty}),
        dial_payload,
    )
    _register(
        hass,
        SERVICE_COMPOSE_SMS,
        _schema(
            {
                vol.Optional("recipient", default=""): cv.string,
                vol.Optional("message", default=""): cv.string,
            }
        ),
        sms_payload,
    )
    _register(
        hass,
        SERVICE_COMPOSE_EMAIL,
        _schema(
            {
                vol.Optional("to", default=[]): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional("cc", default=[]): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional("bcc", default=[]): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional("subject", default=""): cv.string,
                vol.Optional("body", default=""): cv.string,
            }
        ),
        email_payload,
    )
    _register(
        hass,
        SERVICE_CREATE_CALENDAR_EVENT,
        _schema(
            {
                vol.Required("title"): _non_empty,
                vol.Required("start"): cv.datetime,
                vol.Required("end"): cv.datetime,
                vol.Optional("all_day", default=False): cv.boolean,
                vol.Optional("location"): cv.string,
                vol.Optional("description"): cv.string,
                vol.Optional("attendees"): vol.All(cv.ensure_list, [cv.string]),
            }
        ),
        calendar_payload,
    )
    _register(
        hass,
        SERVICE_WEB_SEARCH,
        _schema({vol.Required("query"): _non_empty}),
        web_search_payload,
    )
    _register(
        hass,
        SERVICE_OPEN_SETTINGS,
        _schema({vol.Required("page"): vol.In(SETTINGS)}),
        settings_payload,
    )
    _register(
        hass,
        SERVICE_OPEN_APP_SETTINGS,
        _schema(
            {
                vol.Required("page"): vol.In(APP_SETTINGS),
                vol.Optional("app"): vol.In(set(COMMON_APPS) | {"custom"}),
                vol.Optional("package_name"): _package_id,
            }
        ),
        app_settings_payload,
    )
    _register(hass, SERVICE_OPEN_CAMERA, _schema(), lambda _data: camera_payload())
    _register(
        hass,
        SERVICE_OPEN_VIDEO_CAMERA,
        _schema(),
        lambda _data: camera_payload(video=True),
    )
    _register(
        hass,
        SERVICE_OPEN_ENTITY,
        _schema({vol.Required("entity_id"): cv.entity_id}),
        lambda data: payload(
            "command_webview", {"command": f"entityId:{data['entity_id']}"}
        ),
    )
    _register_find_phone_services(hass)
    _register(
        hass,
        SERVICE_SPEAK,
        _schema(
            {
                vol.Required("message"): _non_empty,
                vol.Optional("playback_mode", default="normal"): vol.In(
                    TTS_PLAYBACK_MODES
                ),
            }
        ),
        lambda data: tts_payload(data["message"], data["playback_mode"]),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK_DEVICE,
        partial(_async_check_device, hass),
        schema=vol.Schema(
            {vol.Required(ATTR_DEVICE_ID): _non_empty}, extra=vol.PREVENT_EXTRA
        ),
        supports_response=SupportsResponse.ONLY,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister integration actions."""
    await async_remove_find_phone_manager(hass)
    for service in list(hass.services.async_services().get(DOMAIN, {})):
        hass.services.async_remove(DOMAIN, service)
