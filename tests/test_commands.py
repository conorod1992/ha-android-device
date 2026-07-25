"""Tests for pure Companion command payload builders."""

from datetime import timedelta

import pytest
import voluptuous as vol

from custom_components.android_device_control.commands import (
    ble_configuration_payload,
    intent_payload,
    raw_payload,
    screen_timeout_payload,
    toggle_payload,
)


def test_toggle_payload() -> None:
    assert toggle_payload("command_flashlight", True) == {
        "message": "command_flashlight",
        "data": {"command": "turn_on"},
    }
    assert toggle_payload("command_flashlight", False)["data"]["command"] == "turn_off"


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
