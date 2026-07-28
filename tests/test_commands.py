"""Tests for pure Companion command payload builders."""

from datetime import time, timedelta

import pytest
import voluptuous as vol

from custom_components.android_device_control.commands import (
    COMMON_APPS,
    ble_configuration_payload,
    dismiss_alarm_payload,
    dismiss_expired_timers_payload,
    intent_payload,
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


def test_toggle_payload() -> None:
    assert toggle_payload("command_flashlight", True) == {
        "message": "command_flashlight",
        "data": {"command": "turn_on"},
    }
    assert toggle_payload("command_flashlight", False)["data"]["command"] == "turn_off"


def test_tts_payload_uses_only_official_playback_fields() -> None:
    assert tts_payload("Hello") == {"message": "TTS", "data": {"tts_text": "Hello"}}
    assert tts_payload("Warning", "alarm_max") == {
        "message": "TTS",
        "data": {
            "tts_text": "Warning",
            "media_stream": "alarm_stream_max",
        },
    }


def test_screen_timeout_converts_to_milliseconds() -> None:
    assert screen_timeout_payload(timedelta(seconds=30)) == {
        "message": "command_screen_off_timeout",
        "data": {"command": 30000},
    }


def test_screen_timeout_rejects_zero() -> None:
    with pytest.raises(vol.Invalid, match="greater than zero"):
        screen_timeout_payload(timedelta())


@pytest.mark.parametrize(
    ("setting", "value", "expected"),
    [
        (
            "advertise_mode",
            "balanced",
            {
                "command": "ble_set_advertise_mode",
                "ble_advertise": "ble_advertise_balanced",
            },
        ),
        (
            "transmit_power",
            "high",
            {"command": "ble_set_transmit_power", "ble_transmit": "ble_transmit_high"},
        ),
        ("uuid", "abc", {"command": "ble_set_uuid", "ble_uuid": "abc"}),
        ("major", "1", {"command": "ble_set_major", "ble_major": "1"}),
        ("minor", "2", {"command": "ble_set_minor", "ble_minor": "2"}),
        (
            "measured_power",
            -75,
            {"command": "ble_set_measured_power", "ble_measured_power": -75},
        ),
    ],
)
def test_ble_configuration(
    setting: str, value: object, expected: dict[str, object]
) -> None:
    assert ble_configuration_payload(setting, value) == {
        "message": "command_ble_transmitter",
        "data": expected,
    }


def test_ble_measured_power_rejects_non_negative() -> None:
    with pytest.raises(vol.Invalid, match="negative"):
        ble_configuration_payload("measured_power", 0)


def test_activity_intent_supports_all_documented_fields() -> None:
    result = intent_payload(
        "command_activity",
        {
            "intent_action": "android.intent.action.VIEW",
            "package_name": "com.example",
            "class_name": "com.example.Main",
            "uri": "example://item/1",
            "mime_type": "text/plain",
            "extras": "count:2:int,enabled:true",
        },
    )
    assert result["data"] == {
        "intent_action": "android.intent.action.VIEW",
        "intent_package_name": "com.example",
        "intent_class_name": "com.example.Main",
        "intent_uri": "example://item/1",
        "intent_type": "text/plain",
        "intent_extras": "count:2:int,enabled:true",
    }


def test_broadcast_requires_package() -> None:
    with pytest.raises(vol.Invalid, match="Package name"):
        intent_payload("command_broadcast_intent", {"intent_action": "example.ACTION"})


def test_intent_rejects_malformed_extras() -> None:
    with pytest.raises(vol.Invalid, match="name:value"):
        intent_payload(
            "command_activity",
            {"intent_action": "example.ACTION", "extras": "missing-separator"},
        )


def test_intent_accepts_structured_extras_and_preserves_raw_compatibility() -> None:
    structured = intent_payload(
        "command_activity",
        {
            "intent_action": "example.ACTION",
            "structured_extras": [
                {"name": "text", "type": "string", "value": "Hello, café"}
            ],
        },
    )
    assert structured["data"]["intent_extras"] == (
        "text:Hello%2C%20caf%C3%A9:String.urlencoded"
    )
    raw = intent_payload(
        "command_activity",
        {"intent_action": "example.ACTION", "extras": "legacy:value:String"},
    )
    assert raw["data"]["intent_extras"] == "legacy:value:String"
    with pytest.raises(vol.Invalid, match="either raw extras"):
        intent_payload(
            "command_activity",
            {
                "intent_action": "example.ACTION",
                "extras": "legacy:value",
                "structured_extras": [
                    {"name": "text", "type": "string", "value": "new"}
                ],
            },
        )


def test_raw_command_guard() -> None:
    assert raw_payload("command_new_feature", {"command": "turn_on"}) == {
        "message": "command_new_feature",
        "data": {"command": "turn_on"},
    }
    assert raw_payload("request_location_update", {}) == {
        "message": "request_location_update"
    }
    with pytest.raises(vol.Invalid, match="Companion commands"):
        raw_payload("ordinary notification", {})


@pytest.mark.parametrize(
    ("alarm_time", "expected"),
    [
        (time(0, 0), "HOUR:0,android.intent.extra.alarm.MINUTES:0"),
        (time(12, 0), "HOUR:12,android.intent.extra.alarm.MINUTES:0"),
        (time(23, 59), "HOUR:23,android.intent.extra.alarm.MINUTES:59"),
    ],
)
def test_set_alarm_converts_time(alarm_time: time, expected: str) -> None:
    result = set_alarm_payload(
        {"alarm_time": alarm_time, "skip_ui": False, "clock_app": "default"}
    )
    assert expected in result["data"]["intent_extras"]
    assert "intent_package_name" not in result["data"]


def test_set_alarm_google_clock_regression_payload() -> None:
    assert set_alarm_payload(
        {
            "alarm_time": time(7, 30),
            "skip_ui": True,
            "clock_app": "google_clock",
        }
    ) == {
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
    }


@pytest.mark.parametrize(
    ("repeat", "numbers"),
    [
        (
            ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "2;3;4;5;6",
        ),
        (["saturday", "sunday"], "7;1"),
        (
            [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ],
            "2;3;4;5;6;7;1",
        ),
    ],
)
def test_set_alarm_repeat_days(repeat: list[str], numbers: str) -> None:
    extras = set_alarm_payload(
        {
            "alarm_time": time(7, 30),
            "repeat": repeat,
            "skip_ui": False,
            "clock_app": "default",
        }
    )["data"]["intent_extras"]
    assert f"android.intent.extra.alarm.DAYS:{numbers}:ArrayList<Integer>" in extras


def test_set_alarm_optional_properties() -> None:
    extras = set_alarm_payload(
        {
            "alarm_time": time(7, 30),
            "label": "Wake, now",
            "vibrate": False,
            "ringtone": "silent",
            "skip_ui": False,
            "clock_app": "custom",
            "clock_package": "com.example.clock",
        }
    )["data"]["intent_extras"]
    assert "MESSAGE:Wake%2C%20now:String.urlencoded" in extras
    assert "VIBRATE:false" in extras
    assert "RINGTONE:silent" in extras


def test_set_alarm_custom_ringtone_and_default_omission() -> None:
    custom = set_alarm_payload(
        {
            "alarm_time": time(7),
            "ringtone": "custom",
            "ringtone_uri": "content://media/alarm/1",
            "skip_ui": False,
        }
    )
    assert "content%3A%2F%2Fmedia%2Falarm%2F1" in custom["data"]["intent_extras"]
    default = set_alarm_payload({"alarm_time": time(7), "skip_ui": False})
    assert "RINGTONE" not in default["data"]["intent_extras"]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"alarm": "next"},
            "android.intent.extra.alarm.SEARCH_MODE:android.next",
        ),
        (
            {"alarm": "label", "alarm_label": "Work"},
            "android.intent.extra.alarm.MESSAGE:Work:String.urlencoded",
        ),
        (
            {"alarm": "time", "alarm_time": time(0, 15)},
            "android.intent.extra.alarm.HOUR:12,"
            "android.intent.extra.alarm.MINUTES:15,"
            "android.intent.extra.alarm.IS_PM:false",
        ),
        (
            {"alarm": "time", "alarm_time": time(12, 15)},
            "android.intent.extra.alarm.HOUR:12,"
            "android.intent.extra.alarm.MINUTES:15,"
            "android.intent.extra.alarm.IS_PM:true",
        ),
        (
            {"alarm": "time", "alarm_time": time(23, 15)},
            "android.intent.extra.alarm.HOUR:11,"
            "android.intent.extra.alarm.MINUTES:15,"
            "android.intent.extra.alarm.IS_PM:true",
        ),
    ],
)
def test_dismiss_alarm_search_modes(data: dict, expected: str) -> None:
    extras = dismiss_alarm_payload(data)["data"]["intent_extras"]
    assert expected in extras


@pytest.mark.parametrize(
    "data",
    [
        {"alarm": "time"},
        {"alarm": "label"},
        {"alarm": "next", "alarm_time": time(8)},
        {"alarm": "next", "alarm_label": "Work"},
    ],
)
def test_dismiss_alarm_invalid_combinations(data: dict) -> None:
    with pytest.raises(vol.Invalid):
        dismiss_alarm_payload(data)


def test_snooze_alarm_duration_and_default() -> None:
    result = snooze_alarm_payload({"duration": timedelta(minutes=15)})
    assert result["data"]["intent_extras"].endswith("SNOOZE_DURATION:15")
    assert "intent_extras" not in snooze_alarm_payload({})["data"]
    with pytest.raises(vol.Invalid, match="whole minutes"):
        snooze_alarm_payload({"duration": timedelta(seconds=61)})


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [
        (timedelta(seconds=1), 1),
        (timedelta(minutes=10), 600),
        (timedelta(hours=2), 7200),
        (timedelta(hours=24), 86400),
    ],
)
def test_set_timer_duration(duration: timedelta, seconds: int) -> None:
    result = set_timer_payload(
        {"duration": duration, "skip_ui": True, "clock_app": "google_clock"}
    )
    assert (
        f"android.intent.extra.alarm.LENGTH:{seconds}"
        in result["data"]["intent_extras"]
    )
    assert result["data"]["intent_package_name"] == "com.google.android.deskclock"


@pytest.mark.parametrize("duration", [timedelta(), timedelta(seconds=86401)])
def test_set_timer_rejects_out_of_range(duration: timedelta) -> None:
    with pytest.raises(vol.Invalid, match="between 1 second and 24 hours"):
        set_timer_payload({"duration": duration, "skip_ui": False})


def test_set_timer_label_and_skip_ui() -> None:
    extras = set_timer_payload(
        {
            "duration": timedelta(minutes=5),
            "label": "Tea",
            "skip_ui": False,
        }
    )["data"]["intent_extras"]
    assert "android.intent.extra.alarm.MESSAGE:Tea:String.urlencoded" in extras
    assert extras.endswith("android.intent.extra.alarm.SKIP_UI:false")


def test_show_and_dismiss_timer_actions() -> None:
    assert show_alarms_payload({})["data"]["intent_action"].endswith("SHOW_ALARMS")
    assert show_timers_payload({})["data"]["intent_action"].endswith("SHOW_TIMERS")
    assert dismiss_expired_timers_payload({})["data"]["intent_action"].endswith(
        "DISMISS_TIMER"
    )


@pytest.mark.parametrize(("package_id", "name"), COMMON_APPS.items())
def test_each_common_app_is_its_canonical_package(package_id: str, name: str) -> None:
    assert name
    assert resolve_launch_package({"app": package_id}) == package_id


def test_launch_app_spotify_custom_and_backwards_compatibility() -> None:
    assert resolve_launch_package({"app": "com.spotify.music"}) == "com.spotify.music"
    assert (
        resolve_launch_package(
            {"app": "custom", "package_name": "com.example.application"}
        )
        == "com.example.application"
    )
    assert (
        resolve_launch_package({"package_name": "com.legacy.app"}) == "com.legacy.app"
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"app": "custom"},
        {"app": "com.spotify.music", "package_name": "com.example"},
    ],
)
def test_launch_app_invalid_combinations(data: dict) -> None:
    with pytest.raises(vol.Invalid):
        resolve_launch_package(data)
