"""Tests for action schemas, mappings, dispatch, and failures."""

import asyncio
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.core import ServiceCall, SupportsResponse
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
        self.responses = {}
        self.failure: Exception | None = None

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
        if self.failure:
            raise self.failure

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
    instance = SimpleNamespace(services=FakeServices(), bus=FakeBus(), data={})
    instance.async_create_task = lambda coro, name: asyncio.create_task(coro, name=name)
    target = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")
    monkeypatch.setattr(
        services_module, "resolve_android_targets", lambda hass, ids: [target]
    )
    services_module.async_register_services(instance)
    yield instance
    await services_module.async_remove_find_phone_manager(instance)
    await services_module.async_remove_notification_manager(instance)


async def call_action(hass: SimpleNamespace, name: str, data: dict) -> dict:
    validated = hass.services.schemas[name]({"device_id": "phone", **data})
    call = ServiceCall(hass, DOMAIN, name, validated)
    await hass.services.handlers[name](call)
    return hass.services.calls[-1][2]


async def test_normal_and_urgent_notification_services_return_dispatch_status(
    hass: SimpleNamespace,
) -> None:
    normal_data = hass.services.schemas["notify"](
        {
            "device_id": "phone",
            "title": "Door",
            "message": "Opened",
            "tag": "door",
            "sticky": True,
            "timeout": 60,
        }
    )
    normal = await hass.services.handlers["notify"](
        ServiceCall(hass, DOMAIN, "notify", normal_data)
    )
    urgent_data = hass.services.schemas["notify_urgent"](
        {"device_id": "phone", "message": "Water leak"}
    )
    urgent = await hass.services.handlers["notify_urgent"](
        ServiceCall(hass, DOMAIN, "notify_urgent", urgent_data)
    )

    assert normal["devices"][0]["dispatched"] is True
    assert hass.services.calls[-2][2]["data"] == {
        "tag": "door",
        "sticky": "true",
        "timeout": 60,
    }
    assert urgent["devices"][0]["dispatched"] is True
    assert hass.services.calls[-1][2]["data"] == {
        "channel": "Urgent",
        "importance": "high",
        "ttl": 0,
        "priority": "high",
    }


async def test_yes_no_wrapper_uses_shared_prompt_tokens(hass: SimpleNamespace) -> None:
    data = hass.services.schemas["ask_yes_no"](
        {
            "device_id": "phone",
            "title": "Door",
            "message": "Lock it?",
            "yes_label": "Lock",
            "no_label": "Leave open",
        }
    )

    response = await hass.services.handlers["ask_yes_no"](
        ServiceCall(hass, DOMAIN, "ask_yes_no", data)
    )
    actions = hass.services.calls[-1][2]["data"]["actions"]

    assert response["devices"][0]["session_id"]
    assert [action["title"] for action in actions] == ["Lock", "Leave open"]
    assert actions[0]["action"] != actions[1]["action"]


def test_choice_schema_rejects_duplicate_ids_and_too_many_choices(
    hass: SimpleNamespace,
) -> None:
    for choices in (
        [{"id": "home", "title": "Home"}, {"id": "home", "title": "Work"}],
        [{"id": str(index), "title": str(index)} for index in range(4)],
    ):
        with pytest.raises(vol.Invalid):
            hass.services.schemas["ask_choice"](
                {
                    "device_id": "phone",
                    "title": "Where?",
                    "message": "Choose",
                    "choices": choices,
                }
            )


async def test_friendly_choice_fields_and_ask_text(hass: SimpleNamespace) -> None:
    choice = await call_action(
        hass,
        "ask_choice",
        {
            "title": "Where?",
            "message": "Choose",
            "choice_1_label": "Home",
            "choice_1_id": "home",
            "choice_2_label": "Work",
            "choice_2_id": "work",
            "require_unlock": True,
        },
    )
    assert [item["title"] for item in choice["data"]["actions"]] == ["Home", "Work"]
    assert all(item["authenticationRequired"] for item in choice["data"]["actions"])

    text = await call_action(
        hass,
        "ask_text",
        {"title": "Name", "message": "Your name?", "reply_label": "Answer"},
    )
    assert text["data"]["actions"][0]["title"] == "Answer"
    assert text["data"]["actions"][0]["behavior"] == "textInput"


async def test_legacy_choices_take_precedence_over_friendly_fields(
    hass: SimpleNamespace,
) -> None:
    outgoing = await call_action(
        hass,
        "ask_choice",
        {
            "title": "Where?",
            "message": "Choose",
            "choices": [{"id": "legacy", "title": "Legacy"}],
            "choice_1_label": "Friendly",
            "choice_1_id": "friendly",
        },
    )
    assert outgoing["data"]["actions"][0]["title"] == "Legacy"


@pytest.mark.parametrize(
    ("service", "field", "value", "command"),
    [
        ("set_ble_advertise_mode", "mode", "balanced", "ble_set_advertise_mode"),
        ("set_ble_transmit_power", "power", "high", "ble_set_transmit_power"),
        ("set_ble_uuid", "uuid", "1234", "ble_set_uuid"),
        ("set_ble_major", "major", 12, "ble_set_major"),
        ("set_ble_minor", "minor", 34, "ble_set_minor"),
        ("set_ble_measured_power", "measured_power", -59, "ble_set_measured_power"),
    ],
)
async def test_dedicated_ble_actions_match_generic_command(
    hass: SimpleNamespace, service: str, field: str, value, command: str
) -> None:
    outgoing = await call_action(hass, service, {field: value})
    assert outgoing["message"] == "command_ble_transmitter"
    assert outgoing["data"]["command"] == command


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
            "speak",
            {"message": "Laundry finished", "playback_mode": "alarm"},
            {
                "message": "TTS",
                "data": {
                    "tts_text": "Laundry finished",
                    "media_stream": "alarm_stream",
                },
            },
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
            "open_entity",
            {"entity_id": "light.kitchen"},
            {
                "message": "command_webview",
                "data": {"command": "entityId:light.kitchen"},
            },
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
            {"device_id": "phone", "brightness": 101}
        )
    with pytest.raises(vol.Invalid):
        hass.services.schemas["set_high_accuracy_interval"](
            {"device_id": "phone", "interval": 4}
        )
    with pytest.raises(vol.Invalid):
        hass.services.schemas["kiosk_set_brightness"](
            {"device_id": "phone", "level": -1}
        )
    with pytest.raises(vol.Invalid):
        hass.services.schemas["kiosk_set_volume"]({"device_id": "phone", "volume": 101})


@pytest.mark.parametrize(
    ("percentage", "raw"),
    [(0, 0), (1, 3), (50, 128), (99, 252), (100, 255)],
)
async def test_screen_brightness_percentage_rounding(
    hass: SimpleNamespace, percentage: int, raw: int
) -> None:
    outgoing = await call_action(
        hass, "set_screen_brightness", {"brightness": percentage}
    )
    assert outgoing["data"]["command"] == raw


def test_screen_brightness_keeps_legacy_raw_level(hass: SimpleNamespace) -> None:
    validated = hass.services.schemas["set_screen_brightness"](
        {"device_id": "phone", "level": 200}
    )
    assert validated["level"] == 200


def test_screen_brightness_rejects_percentage_and_raw_together(
    hass: SimpleNamespace,
) -> None:
    with pytest.raises(vol.Invalid):
        hass.services.schemas["set_screen_brightness"](
            {"device_id": "phone", "brightness": 50, "level": 128}
        )
    with pytest.raises(vol.Invalid):
        hass.services.schemas["set_screen_brightness"]({"device_id": "phone"})


@pytest.mark.parametrize(
    ("mode", "expected_data"),
    [
        ("normal", {"tts_text": "Hello"}),
        ("alarm", {"tts_text": "Hello", "media_stream": "alarm_stream"}),
        (
            "alarm_max",
            {"tts_text": "Hello", "media_stream": "alarm_stream_max"},
        ),
    ],
)
async def test_speak_payload_and_dispatch(
    hass: SimpleNamespace, mode: str, expected_data: dict
) -> None:
    outgoing = await call_action(
        hass, "speak", {"message": "Hello", "playback_mode": mode}
    )
    assert outgoing == {
        "message": "TTS",
        "data": expected_data,
        "target": ["webhook-phone"],
    }


def test_speak_requires_text(hass: SimpleNamespace) -> None:
    with pytest.raises(vol.Invalid):
        hass.services.schemas["speak"]({"device_id": "phone", "message": "   "})


async def test_check_device_returns_response_data(
    monkeypatch: pytest.MonkeyPatch, hass: SimpleNamespace
) -> None:
    expected = {"device_id": "phone", "ready": True}
    monkeypatch.setattr(
        services_module, "inspect_mobile_app_device", lambda _hass, _id: expected
    )
    validated = hass.services.schemas["check_device"]({"device_id": "phone"})
    response = await hass.services.handlers["check_device"](
        ServiceCall(hass, DOMAIN, "check_device", validated, return_response=True)
    )
    assert response == expected
    assert hass.services.responses["check_device"] is SupportsResponse.ONLY
    assert hass.services.calls == []


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


async def test_stale_target_does_not_block_valid_target(
    monkeypatch: pytest.MonkeyPatch, hass: SimpleNamespace
) -> None:
    target = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")

    def resolve(_hass, ids):
        if ids == ["stale"]:
            raise HomeAssistantError("stale target")
        return [target]

    monkeypatch.setattr(services_module, "resolve_android_targets", resolve)

    await hass.services.handlers["stop_tts"](
        ServiceCall(hass, DOMAIN, "stop_tts", {"device_id": ["stale", "phone"]})
    )

    assert [call[2]["target"] for call in hass.services.calls] == [["webhook-phone"]]


async def test_find_phone_defaults_to_immediate_ringtone_notification(
    hass: SimpleNamespace,
) -> None:
    validated = hass.services.schemas["find_phone"]({"device_id": "phone"})
    await hass.services.handlers["find_phone"](
        ServiceCall(hass, DOMAIN, "find_phone", validated)
    )
    assert [call[2]["message"] for call in hass.services.calls] == [
        "command_screen_on",
        "Finding phone",
    ]
    assert hass.services.calls[0][2] == {
        "message": "command_screen_on",
        "data": {"command": "reset"},
        "target": ["webhook-phone"],
    }
    ringtone = hass.services.calls[-1][2]
    assert ringtone["title"] == "Find Phone"
    assert {
        key: ringtone["data"][key] for key in ("ttl", "priority", "channel", "tag")
    } == {
        "ttl": 0,
        "priority": "high",
        "channel": "alarm_stream",
        "tag": "android_device_control_find_phone",
    }
    assert ringtone["data"]["actions"][0]["title"] == "Stop ringing"
    assert "alert_once" not in ringtone["data"]


async def test_find_phone_explicit_ringtone_matches_default(
    hass: SimpleNamespace,
) -> None:
    validated = hass.services.schemas["find_phone"](
        {
            "device_id": "phone",
            "sound_mode": "ringtone",
            "message": "Ignored in ringtone mode",
        }
    )
    await hass.services.handlers["find_phone"](
        ServiceCall(hass, DOMAIN, "find_phone", validated)
    )
    assert hass.services.calls[-1][2]["message"] == "Finding phone"
    assert hass.services.calls[-1][2]["data"]["channel"] == "alarm_stream"


@pytest.mark.parametrize("message", ["Finding phone", "Find café: now, please"])
async def test_find_phone_tts_default_and_custom_message(
    hass: SimpleNamespace, message: str
) -> None:
    data = {"device_id": "phone", "sound_mode": "tts"}
    if message != "Finding phone":
        data["message"] = message
    validated = hass.services.schemas["find_phone"](data)
    await hass.services.handlers["find_phone"](
        ServiceCall(hass, DOMAIN, "find_phone", validated)
    )
    assert hass.services.calls[-1][2] == {
        "message": "TTS",
        "data": {
            "tts_text": message,
            "media_stream": "alarm_stream_max",
        },
        "target": ["webhook-phone"],
    }


async def test_find_phone_optional_flashlight_is_ordered(hass: SimpleNamespace) -> None:
    validated = hass.services.schemas["find_phone"](
        {
            "device_id": "phone",
            "wake_screen": False,
            "flashlight": True,
            "sound_mode": "tts",
            "message": "Find me",
            "show_stop_action": False,
        }
    )
    await hass.services.handlers["find_phone"](
        ServiceCall(hass, DOMAIN, "find_phone", validated)
    )
    assert [call[2]["message"] for call in hass.services.calls] == [
        "command_flashlight",
        "TTS",
    ]


async def test_find_phone_stable_tag_still_alerts_each_call(
    hass: SimpleNamespace,
) -> None:
    for _ in range(2):
        validated = hass.services.schemas["find_phone"]({"device_id": "phone"})
        await hass.services.handlers["find_phone"](
            ServiceCall(hass, DOMAIN, "find_phone", validated)
        )
    ringtone_calls = [
        call[2] for call in hass.services.calls if call[2]["message"] == "Finding phone"
    ]
    assert len(ringtone_calls) == 2
    assert {call["data"]["tag"] for call in ringtone_calls} == {
        "android_device_control_find_phone"
    }
    assert all("alert_once" not in call["data"] for call in ringtone_calls)


async def test_find_phone_dispatches_to_each_device_independently(
    monkeypatch: pytest.MonkeyPatch, hass: SimpleNamespace
) -> None:
    second = AndroidTarget(
        "tablet", "Pixel Tablet", "webhook-tablet", "mobile_app_pixel_tablet"
    )
    first = AndroidTarget("phone", "Pixel 9", "webhook-phone", "mobile_app_pixel_9")
    monkeypatch.setattr(
        services_module, "resolve_android_targets", lambda _hass, _ids: [first, second]
    )
    validated = hass.services.schemas["find_phone"](
        {"device_id": ["phone", "tablet"], "wake_screen": False}
    )
    await hass.services.handlers["find_phone"](
        ServiceCall(hass, DOMAIN, "find_phone", validated)
    )
    assert [call[2]["target"] for call in hass.services.calls] == [
        ["webhook-phone"],
        ["webhook-tablet"],
    ]


def test_find_phone_rejects_invalid_sound_mode(hass: SimpleNamespace) -> None:
    with pytest.raises(vol.Invalid):
        hass.services.schemas["find_phone"](
            {"device_id": "phone", "sound_mode": "loop_forever"}
        )


def test_find_phone_repeat_defaults_and_legacy_yaml(hass: SimpleNamespace) -> None:
    validated = hass.services.schemas["find_phone"]({"device_id": "phone"})
    assert validated["repeat"] is True
    assert validated["max_attempts"] == 10
    assert validated["repeat_interval"].total_seconds() == 15
    assert validated["show_stop_action"] is True
    assert validated["stop_when_unlocked"] is True


@pytest.mark.parametrize("max_attempts", [0, 101])
def test_find_phone_rejects_attempt_bounds(
    hass: SimpleNamespace, max_attempts: int
) -> None:
    with pytest.raises(vol.Invalid):
        hass.services.schemas["find_phone"](
            {"device_id": "phone", "max_attempts": max_attempts}
        )


@pytest.mark.parametrize(
    "repeat_interval",
    [{"seconds": 2}, {"minutes": 10, "seconds": 1}],
)
def test_find_phone_rejects_interval_bounds(
    hass: SimpleNamespace, repeat_interval: dict
) -> None:
    with pytest.raises(vol.Invalid):
        hass.services.schemas["find_phone"](
            {"device_id": "phone", "repeat_interval": repeat_interval}
        )


async def test_find_phone_repeat_false_sends_exactly_one_sound(
    hass: SimpleNamespace,
) -> None:
    validated = hass.services.schemas["find_phone"](
        {"device_id": "phone", "repeat": False}
    )
    await hass.services.handlers["find_phone"](
        ServiceCall(hass, DOMAIN, "find_phone", validated)
    )
    assert (
        sum(call[2]["message"] == "Finding phone" for call in hass.services.calls) == 1
    )


async def test_stop_find_phone_without_session_is_conservative(
    hass: SimpleNamespace,
) -> None:
    validated = hass.services.schemas["stop_find_phone"]({"device_id": "phone"})
    await hass.services.handlers["stop_find_phone"](
        ServiceCall(hass, DOMAIN, "stop_find_phone", validated)
    )
    assert [call[2]["message"] for call in hass.services.calls] == [
        "clear_notification",
        "clear_notification",
    ]
    assert {call[2]["data"]["tag"] for call in hass.services.calls} == {
        "android_device_control_find_phone",
        "find_phone",
    }


async def test_stop_find_phone_can_explicitly_clean_flashlight_after_restart(
    hass: SimpleNamespace,
) -> None:
    validated = hass.services.schemas["stop_find_phone"](
        {"device_id": "phone", "turn_off_flashlight": True}
    )
    await hass.services.handlers["stop_find_phone"](
        ServiceCall(hass, DOMAIN, "stop_find_phone", validated)
    )
    assert [call[2]["message"] for call in hass.services.calls] == [
        "clear_notification",
        "clear_notification",
        "command_flashlight",
    ]
    assert hass.services.calls[-1][2]["data"]["command"] == "turn_off"


async def test_stop_find_phone_is_idempotent(hass: SimpleNamespace) -> None:
    validated = hass.services.schemas["stop_find_phone"]({"device_id": "phone"})
    call = ServiceCall(hass, DOMAIN, "stop_find_phone", validated)

    await hass.services.handlers["stop_find_phone"](call)
    await hass.services.handlers["stop_find_phone"](call)

    assert [call[2]["message"] for call in hass.services.calls] == [
        "clear_notification",
        "clear_notification",
        "clear_notification",
        "clear_notification",
    ]


async def test_unregister_cancels_find_phone_and_removes_listeners_and_services(
    hass: SimpleNamespace,
) -> None:
    validated = hass.services.schemas["find_phone"]({"device_id": "phone"})
    await hass.services.handlers["find_phone"](
        ServiceCall(hass, DOMAIN, "find_phone", validated)
    )
    manager = hass.data[DOMAIN][services_module.DATA_FIND_PHONE_MANAGER]
    session = manager.sessions["phone"]

    await services_module.async_unregister_services(hass)

    assert session.task.cancelled()
    assert hass.bus.listeners == []
    assert hass.services.handlers == {}
    assert DOMAIN not in hass.data


async def test_find_phone_total_transport_failure_raises(
    hass: SimpleNamespace,
) -> None:
    hass.services.failure = HomeAssistantError("transport failed")
    validated = hass.services.schemas["find_phone"]({"device_id": "phone"})
    with pytest.raises(HomeAssistantError):
        await hass.services.handlers["find_phone"](
            ServiceCall(hass, DOMAIN, "find_phone", validated)
        )
    manager = hass.data[DOMAIN][services_module.DATA_FIND_PHONE_MANAGER]
    assert manager.sessions == {}
    assert [call[2]["message"] for call in hass.services.calls] == [
        "command_screen_on",
        "Finding phone",
    ]
