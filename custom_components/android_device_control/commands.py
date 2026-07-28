"""Pure payload builders for Android Companion notification commands."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import quote

import voluptuous as vol

from .apps import COMMON_APPS as _COMMON_APPS
from .apps import resolve_app
from .intents import merge_extras

COMMON_APPS = _COMMON_APPS

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
TTS_PLAYBACK_MODES = {
    "normal": None,
    "alarm": "alarm_stream",
    "alarm_max": "alarm_stream_max",
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

ALARM_ACTION_SET = "android.intent.action.SET_ALARM"
ALARM_ACTION_DISMISS = "android.intent.action.DISMISS_ALARM"
ALARM_ACTION_SNOOZE = "android.intent.action.SNOOZE_ALARM"
ALARM_ACTION_SHOW = "android.intent.action.SHOW_ALARMS"
TIMER_ACTION_SET = "android.intent.action.SET_TIMER"
TIMER_ACTION_DISMISS = "android.intent.action.DISMISS_TIMER"
TIMER_ACTION_SHOW = "android.intent.action.SHOW_TIMERS"

EXTRA_HOUR = "android.intent.extra.alarm.HOUR"
EXTRA_MINUTES = "android.intent.extra.alarm.MINUTES"
EXTRA_DAYS = "android.intent.extra.alarm.DAYS"
EXTRA_MESSAGE = "android.intent.extra.alarm.MESSAGE"
EXTRA_VIBRATE = "android.intent.extra.alarm.VIBRATE"
EXTRA_RINGTONE = "android.intent.extra.alarm.RINGTONE"
EXTRA_SKIP_UI = "android.intent.extra.alarm.SKIP_UI"
EXTRA_SEARCH_MODE = "android.intent.extra.alarm.SEARCH_MODE"
EXTRA_IS_PM = "android.intent.extra.alarm.IS_PM"
EXTRA_SNOOZE_DURATION = "android.intent.extra.alarm.SNOOZE_DURATION"
EXTRA_LENGTH = "android.intent.extra.alarm.LENGTH"

WEEKDAY_NUMBERS = {
    "sunday": 1,
    "monday": 2,
    "tuesday": 3,
    "wednesday": 4,
    "thursday": 5,
    "friday": 6,
    "saturday": 7,
}
ALARM_SEARCH_MODES = {
    "next": "android.next",
    "time": "android.time",
    "label": "android.label",
}
NOON_HOUR = 12
MAX_TIMER_SECONDS = 86400


def payload(message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the outgoing Mobile App notify payload."""
    result: dict[str, Any] = {"message": message}
    if data:
        result["data"] = data
    return result


def tts_payload(text: str, playback_mode: str = "normal") -> dict[str, Any]:
    """Build an official Android Companion text-to-speech notification."""
    data = {"tts_text": text}
    if media_stream := TTS_PLAYBACK_MODES[playback_mode]:
        data["media_stream"] = media_stream
    return payload("TTS", data)


def _string_extra(name: str, value: str) -> str:
    """Encode an arbitrary string for Companion's comma-separated extra format."""
    return f"{name}:{quote(value, safe='')}:String.urlencoded"


def _activity_payload(
    action: str,
    extras: list[str] | None = None,
    package_name: str | None = None,
) -> dict[str, Any]:
    """Build a standard AlarmClock activity command."""
    data = {"intent_action": action}
    if extras:
        data["intent_extras"] = ",".join(extras)
    if package_name:
        data["intent_package_name"] = package_name
    return payload("command_activity", data)


def resolve_launch_package(data: dict[str, Any]) -> str:
    """Resolve an app preset or a backwards-compatible raw package name."""
    return resolve_app(data)


def clock_package(data: dict[str, Any]) -> str | None:
    """Resolve optional AlarmClock package targeting."""
    selection = data.get("clock_app", "default")
    custom = data.get("clock_package", "").strip()
    if selection == "custom":
        if not custom:
            raise vol.Invalid("Clock package is required for Custom package")
        return custom
    if custom:
        raise vol.Invalid("Clock package is only used with Custom package")
    if selection == "google_clock":
        return "com.google.android.deskclock"
    return None


def set_alarm_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build an Android AlarmClock set-alarm intent."""
    alarm_time = data["alarm_time"]
    extras = [f"{EXTRA_HOUR}:{alarm_time.hour}", f"{EXTRA_MINUTES}:{alarm_time.minute}"]
    if label := data.get("label", "").strip():
        extras.append(_string_extra(EXTRA_MESSAGE, label))
    if days := data.get("repeat"):
        values = ";".join(str(WEEKDAY_NUMBERS[day]) for day in days)
        extras.append(f"{EXTRA_DAYS}:{values}:ArrayList<Integer>")
    if "vibrate" in data:
        extras.append(f"{EXTRA_VIBRATE}:{str(data['vibrate']).lower()}")
    ringtone = data.get("ringtone", "default")
    if ringtone == "silent":
        extras.append(f"{EXTRA_RINGTONE}:silent")
    elif ringtone == "custom":
        uri = data.get("ringtone_uri", "").strip()
        if not uri:
            raise vol.Invalid("Ringtone URI is required for Custom ringtone")
        extras.append(_string_extra(EXTRA_RINGTONE, uri))
    elif data.get("ringtone_uri", "").strip():
        raise vol.Invalid("Ringtone URI is only used with Custom ringtone")
    extras.append(f"{EXTRA_SKIP_UI}:{str(data['skip_ui']).lower()}")
    return _activity_payload(ALARM_ACTION_SET, extras, clock_package(data))


def dismiss_alarm_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build a standard alarm search and dismiss intent."""
    mode = data["alarm"]
    extras = [f"{EXTRA_SEARCH_MODE}:{ALARM_SEARCH_MODES[mode]}"]
    alarm_time = data.get("alarm_time")
    label = data.get("alarm_label", "").strip()
    if mode == "time":
        if alarm_time is None:
            raise vol.Invalid("Alarm time is required when dismissing by time")
        hour_12 = alarm_time.hour % 12 or 12
        extras.extend(
            [
                f"{EXTRA_HOUR}:{hour_12}",
                f"{EXTRA_MINUTES}:{alarm_time.minute}",
                f"{EXTRA_IS_PM}:{str(alarm_time.hour >= NOON_HOUR).lower()}",
            ]
        )
    elif alarm_time is not None:
        raise vol.Invalid("Alarm time is only used with Alarm at time")
    if mode == "label":
        if not label:
            raise vol.Invalid("Alarm label is required when dismissing by label")
        extras.append(_string_extra(EXTRA_MESSAGE, label))
    elif label:
        raise vol.Invalid("Alarm label is only used with Alarm with label")
    return _activity_payload(ALARM_ACTION_DISMISS, extras, clock_package(data))


def snooze_alarm_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build a standard snooze-currently-ringing-alarm intent."""
    extras = []
    if duration := data.get("duration"):
        seconds = duration.total_seconds()
        if seconds % 60:
            raise vol.Invalid("Snooze duration must use whole minutes")
        extras.append(f"{EXTRA_SNOOZE_DURATION}:{int(seconds // 60)}")
    return _activity_payload(ALARM_ACTION_SNOOZE, extras, clock_package(data))


def set_timer_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build an Android AlarmClock set-timer intent."""
    seconds = int(data["duration"].total_seconds())
    if not 1 <= seconds <= MAX_TIMER_SECONDS:
        raise vol.Invalid("Timer duration must be between 1 second and 24 hours")
    extras = [f"{EXTRA_LENGTH}:{seconds}"]
    if label := data.get("label", "").strip():
        extras.append(_string_extra(EXTRA_MESSAGE, label))
    extras.append(f"{EXTRA_SKIP_UI}:{str(data['skip_ui']).lower()}")
    return _activity_payload(TIMER_ACTION_SET, extras, clock_package(data))


def show_alarms_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build a standard show-alarms intent."""
    return _activity_payload(ALARM_ACTION_SHOW, package_name=clock_package(data))


def dismiss_expired_timers_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Dismiss every expired timer, as defined by Android's no-URI behavior."""
    return _activity_payload(TIMER_ACTION_DISMISS, package_name=clock_package(data))


def show_timers_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build a standard show-timers intent."""
    return _activity_payload(TIMER_ACTION_SHOW, package_name=clock_package(data))


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
    extras = merge_extras(data.get("extras", ""), data.get("structured_extras", []))
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
