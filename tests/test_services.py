"""Tests for action schemas, mappings, dispatch, and failures."""

from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.android_device_control import services as services_module
from custom_components.android_device_control.const import DOMAIN
from custom_components.android_device_control.device import AndroidTarget


class FakeServices:
    """Minimal action registry used by service tests."""

    def __init__(self) -> None:
        self.handlers = {}
        self.schemas = {}
        self.calls = []
        self.failure: Exception | None = None

    def async_register(self, domain, name, handler, *, schema) -> None:
        self.handlers[name] = handler
        self.schemas[name] = schema

    async def async_call(self, domain, service, data, *, blocking) -> None:
        self.calls.append((domain, service, data, blocking))
        if self.failure:
            raise self.failure


@pytest.fixture
def hass(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    instance = SimpleNamespace(services=FakeServices())
    target = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")
    monkeypatch.setattr(
        services_module, "resolve_android_targets", lambda hass, ids: [target]
    )
    services_module.async_register_services(instance)
    return instance


async def call_action(hass: SimpleNamespace, name: str, data: dict) -> dict:
    validated = hass.services.schemas[name]({"device_id": "phone", **data})
    call = ServiceCall(hass, DOMAIN, name, validated)
    await hass.services.handlers[name](call)
    return hass.services.calls[-1][2]


@pytest.mark.parametrize(
    ("service", "data", "expected"),
    [
        (
            "set_ringer_mode",
            {"mode": "vibrate"},
            {"message": "command_ringer_mode", "data": {"command": "vibrate"}},
        ),
        (
            "set_volume",
            {"stream": "media", "level": 10},
            {
                "message": "command_volume_level",
                "data": {"media_stream": "music_stream", "command": 10},
            },
        ),
        (
            "set_do_not_disturb",
            {"mode": "priority_only"},
            {"message": "command_dnd", "data": {"command": "priority_only"}},
        ),
        (
            "media_control",
            {"media_command": "pause", "package_name": "com.spotify.music"},
            {
                "message": "command_media",
                "data": {
                    "media_command": "pause",
                    "media_package_name": "com.spotify.music",
                },
            },
        ),
        ("stop_tts", {}, {"message": "command_stop_tts"}),
        (
            "turn_screen_on",
            {"keep_screen_on": True},
            {"message": "command_screen_on", "data": {"command": "keep_screen_on"}},
        ),
        (
            "set_screen_brightness",
            {"level": 200},
            {"message": "command_screen_brightness_level", "data": {"command": 200}},
        ),
        (
            "set_auto_brightness",
            {"enabled": True},
            {
                "message": "command_auto_screen_brightness",
                "data": {"command": "turn_on"},
            },
        ),
        (
            "set_screen_timeout",
            {"duration": {"seconds": 30}},
            {"message": "command_screen_off_timeout", "data": {"command": 30000}},
        ),
        (
            "open_webview",
            {"path": "entityId:sun.sun"},
            {"message": "command_webview", "data": {"command": "entityId:sun.sun"}},
        ),
        (
            "set_flashlight",
            {"enabled": False},
            {"message": "command_flashlight", "data": {"command": "turn_off"}},
        ),
        (
            "set_bluetooth",
            {"enabled": True},
            {"message": "command_bluetooth", "data": {"command": "turn_on"}},
        ),
        (
            "set_ble_transmitter",
            {"enabled": True},
            {"message": "command_ble_transmitter", "data": {"command": "turn_on"}},
        ),
        (
            "configure_ble_transmitter",
            {"setting": "advertise_mode", "value": "balanced"},
            {
                "message": "command_ble_transmitter",
                "data": {
                    "command": "ble_set_advertise_mode",
                    "ble_advertise": "ble_advertise_balanced",
                },
            },
        ),
        (
            "set_beacon_monitor",
            {"enabled": True},
            {"message": "command_beacon_monitor", "data": {"command": "turn_on"}},
        ),
        ("request_location_update", {}, {"message": "request_location_update"}),
        ("update_sensors", {}, {"message": "command_update_sensors"}),
        (
            "set_high_accuracy_mode",
            {"mode": "force_on"},
            {"message": "command_high_accuracy_mode", "data": {"command": "force_on"}},
        ),
        (
            "set_high_accuracy_interval",
            {"interval": 60},
            {
                "message": "command_high_accuracy_mode",
                "data": {
                    "command": "high_accuracy_set_update_interval",
                    "high_accuracy_update_interval": 60,
                },
            },
        ),
        (
            "launch_app",
            {"package_name": "com.example"},
            {"message": "command_launch_app", "data": {"package_name": "com.example"}},
        ),
        (
            "launch_app",
            {"app": "com.spotify.music"},
            {
                "message": "command_launch_app",
                "data": {"package_name": "com.spotify.music"},
            },
        ),
        (
            "set_alarm",
            {
                "alarm_time": "07:30:00",
                "skip_ui": True,
                "clock_app": "google_clock",
            },
            {
                "message": "command_activity",
                "data": {
                    "intent_action": "android.intent.action.SET_ALARM",
                    "intent_extras": (
                        "android.intent.extra.alarm.HOUR:7,"
                        "android.intent.extra.alarm.MINUTES:30,"
                        "android.intent.extra.alarm.SKIP_UI:true"
                    ),
                    "intent_package_name": "com.google.android.deskclock",
                },
            },
        ),
        (
            "dismiss_alarm",
            {"alarm": "next"},
            {
                "message": "command_activity",
                "data": {
                    "intent_action": "android.intent.action.DISMISS_ALARM",
                    "intent_extras": (
                        "android.intent.extra.alarm.SEARCH_MODE:android.next"
                    ),
                },
            },
        ),
        (
            "snooze_alarm",
            {"duration": {"minutes": 10}},
            {
                "message": "command_activity",
                "data": {
                    "intent_action": "android.intent.action.SNOOZE_ALARM",
                    "intent_extras": ("android.intent.extra.alarm.SNOOZE_DURATION:10"),
                },
            },
        ),
        (
            "show_alarms",
            {},
            {
                "message": "command_activity",
                "data": {"intent_action": "android.intent.action.SHOW_ALARMS"},
            },
        ),
        (
            "set_timer",
            {"duration": {"minutes": 5}},
            {
                "message": "command_activity",
                "data": {
                    "intent_action": "android.intent.action.SET_TIMER",
                    "intent_extras": (
                        "android.intent.extra.alarm.LENGTH:300,"
                        "android.intent.extra.alarm.SKIP_UI:false"
                    ),
                },
            },
        ),
        (
            "dismiss_expired_timers",
            {},
            {
                "message": "command_activity",
                "data": {"intent_action": "android.intent.action.DISMISS_TIMER"},
            },
        ),
        (
            "show_timers",
            {},
            {
                "message": "command_activity",
                "data": {"intent_action": "android.intent.action.SHOW_TIMERS"},
            },
        ),
        (
            "launch_activity",
            {"intent_action": "android.intent.action.VIEW", "uri": "geo:0,0"},
            {
                "message": "command_activity",
                "data": {
                    "intent_action": "android.intent.action.VIEW",
                    "intent_uri": "geo:0,0",
                },
            },
        ),
        (
            "set_app_lock",
            {"app_lock_enabled": True, "app_lock_timeout": 60},
            {
                "message": "command_app_lock",
                "data": {"app_lock_enabled": True, "app_lock_timeout": 60},
            },
        ),
        (
            "set_wake_word_detection",
            {"enabled": True},
            {"message": "command_wake_word_detection", "data": {"command": "turn_on"}},
        ),
        (
            "set_persistent_connection",
            {"mode": "home_wifi"},
            {
                "message": "command_persistent_connection",
                "data": {"persistent": "home_wifi"},
            },
        ),
        (
            "send_broadcast_intent",
            {"intent_action": "example.ACTION", "package_name": "com.example"},
            {
                "message": "command_broadcast_intent",
                "data": {
                    "intent_action": "example.ACTION",
                    "intent_package_name": "com.example",
                },
            },
        ),
        (
            "clear_notification",
            {"tag": "alarm"},
            {"message": "clear_notification", "data": {"tag": "alarm"}},
        ),
        (
            "remove_notification_channel",
            {"channel": "Motion"},
            {"message": "remove_channel", "data": {"channel": "Motion"}},
        ),
        ("kiosk_show_screensaver", {}, {"message": "kiosk_show_screensaver"}),
        ("kiosk_hide_screensaver", {}, {"message": "kiosk_hide_screensaver"}),
        (
            "kiosk_show_camera",
            {"entity_id": "camera.front_door"},
            {
                "message": "kiosk_show_camera",
                "data": {"entity_id": "camera.front_door"},
            },
        ),
        ("kiosk_hide_camera", {}, {"message": "kiosk_hide_camera"}),
        (
            "kiosk_set_brightness",
            {"level": 20},
            {"message": "kiosk_set_brightness", "data": {"level": 20.0}},
        ),
        (
            "kiosk_set_volume",
            {"volume": 30},
            {"message": "kiosk_set_volume", "data": {"volume": 30.0}},
        ),
        ("kiosk_reload", {}, {"message": "kiosk_reload"}),
        ("kiosk_default", {}, {"message": "kiosk_default"}),
        (
            "send_command",
            {"command": "command_future", "data": {"answer": 42}},
            {"message": "command_future", "data": {"answer": 42}},
        ),
    ],
)
async def test_every_action_payload(
    hass: SimpleNamespace, service: str, data: dict, expected: dict
) -> None:
    outgoing = await call_action(hass, service, data)
    assert {key: outgoing[key] for key in expected} == expected
    assert outgoing["target"] == ["webhook-phone"]


def test_numeric_bounds(hass: SimpleNamespace) -> None:
    with pytest.raises(vol.Invalid):
        hass.services.schemas["set_screen_brightness"](
            {"device_id": "phone", "level": 256}
        )
    with pytest.raises(vol.Invalid):
        hass.services.schemas["set_high_accuracy_interval"](
            {"device_id": "phone", "interval": 4}
        )


@pytest.mark.parametrize("package_name", ["", "spotify", "com.example-app", "a..b"])
def test_launch_app_rejects_invalid_package_ids(
    hass: SimpleNamespace, package_name: str
) -> None:
    with pytest.raises(vol.Invalid):
        hass.services.schemas["launch_app"](
            {
                "device_id": "phone",
                "app": "custom",
                "package_name": package_name,
            }
        )


async def test_underlying_notify_failure_is_reported(hass: SimpleNamespace) -> None:
    hass.services.failure = HomeAssistantError("transport failed")
    with pytest.raises(HomeAssistantError):
        await call_action(hass, "set_ringer_mode", {"mode": "normal"})


async def test_all_targets_are_resolved_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, hass: SimpleNamespace
) -> None:
    monkeypatch.setattr(
        services_module,
        "resolve_android_targets",
        lambda hass, ids: (_ for _ in ()).throw(HomeAssistantError("bad target")),
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.handlers["stop_tts"](
            ServiceCall(hass, DOMAIN, "stop_tts", {"device_id": ["phone", "bad"]})
        )
    assert hass.services.calls == []
