"""Pure payload builders for Android Companion notification commands."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

TOGGLE_COMMANDS = {False: "turn_off", True: "turn_on"}
RINGER_MODES = {"normal", "vibrate", "silent"}
DND_MODES = {"off", "priority_only", "alarms_only", "total_silence"}
MEDIA_COMMANDS = {
    "fast_forward",
    "next",
    "pause",
    "play",
    "play_pause",
    "previous",
    "rewind",
    "stop",
}
VOLUME_STREAMS = {
    "alarm": "alarm_stream",
    "call": "call_stream",
    "dtmf": "dtmf_stream",
    "media": "music_stream",
    "notification": "notification_stream",
    "ring": "ring_stream",
    "system": "system_stream",
    "assistant": "assistant_stream",
}
HIGH_ACCURACY_MODES = {"turn_off", "turn_on", "force_off", "force_on"}
PERSISTENT_MODES = {"always", "home_wifi", "screen_on", "never"}
BLE_SETTINGS = {
    "advertise_mode": (
        "ble_set_advertise_mode",
        "ble_advertise",
        {
            "low_latency": "ble_advertise_low_latency",
            "balanced": "ble_advertise_balanced",
            "low_power": "ble_advertise_low_power",
        },
    ),
    "transmit_power": (
        "ble_set_transmit_power",
        "ble_transmit",
        {
            "high": "ble_transmit_high",
            "medium": "ble_transmit_medium",
            "low": "ble_transmit_low",
            "ultra_low": "ble_transmit_ultra_low",
        },
    ),
    "uuid": ("ble_set_uuid", "ble_uuid", None),
    "major": ("ble_set_major", "ble_major", None),
    "minor": ("ble_set_minor", "ble_minor", None),
    "measured_power": ("ble_set_measured_power", "ble_measured_power", None),
}
RAW_MESSAGE_PREFIX = "command_"
RAW_EXACT_MESSAGES = {
    "request_location_update",
    "clear_notification",
    "remove_channel",
    "kiosk_show_screensaver",
    "kiosk_hide_screensaver",
    "kiosk_show_camera",
    "kiosk_hide_camera",
    "kiosk_set_brightness",
    "kiosk_set_volume",
    "kiosk_reload",
    "kiosk_default",
}


def payload(message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the outgoing Mobile App notify payload."""
    result: dict[str, Any] = {"message": message}
    if data:
        result["data"] = data
    return result


def toggle_payload(message: str, enabled: bool) -> dict[str, Any]:
    """Build a standard turn_on/turn_off command."""
    return payload(message, {"command": TOGGLE_COMMANDS[enabled]})


def screen_timeout_payload(duration: timedelta) -> dict[str, Any]:
    """Convert a friendly duration to Android milliseconds."""
    milliseconds = int(duration.total_seconds() * 1000)
    if milliseconds <= 0:
        raise vol.Invalid("Screen timeout must be greater than zero")
    return payload("command_screen_off_timeout", {"command": milliseconds})


def intent_payload(message: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build and validate an activity or broadcast intent payload."""
    action = data["intent_action"].strip()
    package = data.get("package_name", "").strip()
    class_name = data.get("class_name", "").strip()
    extras = data.get("extras", "").strip()
    if not action:
        raise vol.Invalid("Intent action must not be empty")
    if message == "command_broadcast_intent" and not package:
        raise vol.Invalid("Package name is required for broadcast intents")
    if class_name and not package:
        raise vol.Invalid("Package name is required when class name is set")
    if extras:
        for extra in extras.split(","):
            if ":" not in extra or not extra.split(":", 1)[0]:
                raise vol.Invalid("Each intent extra must use name:value format")
    command_data: dict[str, Any] = {"intent_action": action}
    optional = {
        "intent_package_name": package,
        "intent_class_name": class_name,
        "intent_uri": data.get("uri", "").strip(),
        "intent_type": data.get("mime_type", "").strip(),
        "intent_extras": extras,
    }
    command_data.update({key: value for key, value in optional.items() if value})
    return payload(message, command_data)


def ble_configuration_payload(setting: str, value: Any) -> dict[str, Any]:
    """Build a BLE transmitter configuration payload."""
    command, field, mapping = BLE_SETTINGS[setting]
    mapped = mapping.get(value) if mapping else value
    if mapped is None or str(mapped).strip() == "":
        raise vol.Invalid(f"A value is required for BLE setting {setting}")
    if setting == "measured_power" and int(mapped) >= 0:
        raise vol.Invalid("BLE measured power must be a negative number")
    return payload("command_ble_transmitter", {"command": command, field: mapped})


def raw_payload(message: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build a guarded raw command payload."""
    message = message.strip()
    if not message.startswith(RAW_MESSAGE_PREFIX) and message not in RAW_EXACT_MESSAGES:
        raise vol.Invalid(
            "Raw messages must be Companion commands "
            "(command_* or a documented special command)"
        )
    return payload(message, data)
