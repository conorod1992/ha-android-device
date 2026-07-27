"""Pure builders for friendly, documented Android intents."""

from __future__ import annotations

from datetime import UTC, datetime, time
from math import isfinite
from typing import Any, Final
from urllib.parse import quote, urlencode, urlsplit

import voluptuous as vol
from homeassistant.util import dt as dt_util

from .apps import resolve_app

ACTION_VIEW = "android.intent.action.VIEW"
ACTION_DIAL = "android.intent.action.DIAL"
ACTION_SENDTO = "android.intent.action.SENDTO"
ACTION_INSERT = "android.intent.action.INSERT"
ACTION_WEB_SEARCH = "android.intent.action.WEB_SEARCH"
ACTION_IMAGE_CAPTURE = "android.media.action.IMAGE_CAPTURE"
ACTION_VIDEO_CAPTURE = "android.media.action.VIDEO_CAPTURE"

SETTINGS: Final[dict[str, tuple[str, int]]] = {
    "main": ("android.settings.SETTINGS", 1),
    "wifi": ("android.settings.WIFI_SETTINGS", 1),
    "bluetooth": ("android.settings.BLUETOOTH_SETTINGS", 1),
    "nfc": ("android.settings.NFC_SETTINGS", 16),
    "display": ("android.settings.DISPLAY_SETTINGS", 1),
    "sound": ("android.settings.SOUND_SETTINGS", 1),
    "location": ("android.settings.LOCATION_SOURCE_SETTINGS", 1),
    "battery": ("android.intent.action.POWER_USAGE_SUMMARY", 1),
    "apps": ("android.settings.MANAGE_APPLICATIONS_SETTINGS", 3),
    "default_apps": ("android.settings.MANAGE_DEFAULT_APPS_SETTINGS", 24),
    "accessibility": ("android.settings.ACCESSIBILITY_SETTINGS", 5),
    "notification_settings": ("android.settings.NOTIFICATION_SETTINGS", 21),
    "notification_access": (
        "android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS",
        22,
    ),
    "dnd_access": ("android.settings.NOTIFICATION_POLICY_ACCESS_SETTINGS", 23),
    "overlay_access": ("android.settings.action.MANAGE_OVERLAY_PERMISSION", 23),
    "modify_system_settings": ("android.settings.action.MANAGE_WRITE_SETTINGS", 23),
    "developer_options": ("android.settings.APPLICATION_DEVELOPMENT_SETTINGS", 5),
    "cast": ("android.settings.CAST_SETTINGS", 21),
    "home": ("android.settings.HOME_SETTINGS", 21),
    "night_display": ("android.settings.NIGHT_DISPLAY_SETTINGS", 26),
    "security": ("android.settings.SECURITY_SETTINGS", 1),
    "privacy": ("android.settings.PRIVACY_SETTINGS", 29),
    "vpn": ("android.settings.VPN_SETTINGS", 24),
    "data_usage": ("android.settings.DATA_USAGE_SETTINGS", 28),
}

APP_SETTINGS: Final[dict[str, tuple[str, int, str]]] = {
    "details": ("android.settings.APPLICATION_DETAILS_SETTINGS", 9, "uri"),
    "notifications": ("android.settings.APP_NOTIFICATION_SETTINGS", 26, "extra"),
    "overlay": ("android.settings.action.MANAGE_OVERLAY_PERMISSION", 23, "uri"),
    "modify_system_settings": (
        "android.settings.action.MANAGE_WRITE_SETTINGS",
        23,
        "uri",
    ),
}

EXTRA_TYPES: Final[dict[str, str]] = {
    "string": "String.urlencoded",
    "boolean": "boolean",
    "integer": "int",
    "long": "long",
    "float": "float",
    "double": "double",
    "integer_list": "ArrayList<Integer>",
}
MIN_LATITUDE = -90
MAX_LATITUDE = 90
MIN_LONGITUDE = -180
MAX_LONGITUDE = 180


def _activity(
    action: str,
    *,
    uri: str | None = None,
    package: str | None = None,
    mime_type: str | None = None,
    extras: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"intent_action": action}
    if uri:
        data["intent_uri"] = uri
    if package:
        data["intent_package_name"] = package
    if mime_type:
        data["intent_type"] = mime_type
    if extras:
        data["intent_extras"] = extras
    return {"message": "command_activity", "data": data}


def _serialize_list(name: str, value: Any) -> str:
    if not isinstance(value, list) or not value:
        raise vol.Invalid(f"Extra {name} must be a non-empty list")
    try:
        return ";".join(str(int(entry)) for entry in value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid(f"Extra {name} must contain integers") from err


def _serialize_number(name: str, kind: str, value: Any) -> str:
    if kind in {"integer", "long"}:
        if isinstance(value, bool):
            raise vol.Invalid(f"Extra {name} must be an integer")
        try:
            return str(int(value))
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"Extra {name} must be an integer") from err
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid(f"Extra {name} must be numeric") from err
    if not isfinite(number):
        raise vol.Invalid(f"Extra {name} must be finite")
    return str(number)


def _serialize_value(name: str, kind: str, value: Any) -> str:
    if kind.endswith("_list"):
        return _serialize_list(name, value)
    if kind == "string":
        return quote(str(value), safe="")
    if kind == "boolean":
        if not isinstance(value, bool):
            raise vol.Invalid(f"Extra {name} must be a boolean")
        return str(value).lower()
    return _serialize_number(name, kind, value)


def serialize_extras(items: list[dict[str, Any]]) -> str:
    """Serialize the subset of types understood by Companion's current parser."""
    result: list[str] = []
    for item in items:
        name = str(item.get("name", "")).strip()
        kind = str(item.get("type", "")).strip()
        if not name or any(char in name for char in ",:"):
            raise vol.Invalid(
                "Extra names must be non-empty and cannot contain commas or colons"
            )
        if kind not in EXTRA_TYPES:
            raise vol.Invalid(f"Unsupported intent extra type: {kind}")
        if "value" not in item:
            raise vol.Invalid(f"A value is required for extra {name}")
        encoded = _serialize_value(name, kind, item["value"])
        result.append(f"{name}:{encoded}:{EXTRA_TYPES[kind]}")
    return ",".join(result)


def merge_extras(raw: str, structured: list[dict[str, Any]]) -> str:
    """Preserve raw extras while preventing two ambiguous representations."""
    raw = raw.strip()
    if raw and structured:
        raise vol.Invalid("Use either raw extras or structured extras, not both")
    return serialize_extras(structured) if structured else raw


def open_url_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build an ACTION_VIEW payload for an absolute URI."""
    uri = data["url"].strip()
    parsed = urlsplit(uri)
    if not parsed.scheme or any(char.isspace() for char in uri):
        raise vol.Invalid("Enter an absolute URI with no unescaped spaces")
    package = None
    if data.get("app") or data.get("package_name"):
        package = resolve_app(data, capability="browser")
    return _activity(ACTION_VIEW, uri=uri, package=package)


def _location(data: dict[str, Any]) -> tuple[str, str | None]:
    query = data.get("location", "").strip()
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    if query and (latitude is not None or longitude is not None):
        raise vol.Invalid("Use an address/place or coordinates, not both")
    if query:
        return query, None
    if latitude is None or longitude is None:
        raise vol.Invalid("Provide an address/place or both latitude and longitude")
    latitude, longitude = float(latitude), float(longitude)
    if not MIN_LATITUDE <= latitude <= MAX_LATITUDE or not (
        MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
    ):
        raise vol.Invalid("Coordinates are outside valid latitude/longitude ranges")
    coordinates = f"{latitude:g},{longitude:g}"
    return coordinates, data.get("label", "").strip() or None


def show_map_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build a standard geo or documented provider map payload."""
    destination, label = _location(data)
    provider = data.get("provider", "default")
    if provider == "waze":
        params = {"q": destination}
        return _activity(ACTION_VIEW, uri=f"https://waze.com/ul?{urlencode(params)}")
    query = f"{destination}({label})" if label else destination
    uri = f"geo:0,0?q={quote(query, safe=',')}"
    package = "com.google.android.apps.maps" if provider == "google_maps" else None
    return _activity(ACTION_VIEW, uri=uri, package=package)


def navigate_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build a provider-aware directions payload."""
    destination, label = _location(data)
    provider = data.get("provider", "default")
    mode = data.get("travel_mode", "default")
    if provider == "waze":
        if mode not in {"default", "driving"}:
            raise vol.Invalid("Waze only supports its default driving mode")
        key = "ll" if "," in destination and not data.get("location") else "q"
        query = urlencode({key: destination, "navigate": "yes"})
        return _activity(ACTION_VIEW, uri=f"https://waze.com/ul?{query}")
    if provider == "google_maps":
        params = {"api": "1", "destination": destination, "dir_action": "navigate"}
        if mode != "default":
            params["travelmode"] = mode
        return _activity(
            ACTION_VIEW, uri=f"https://www.google.com/maps/dir/?{urlencode(params)}"
        )
    if mode != "default":
        raise vol.Invalid("Travel mode requires Google Maps or Waze")
    query = f"{destination}({label})" if label else destination
    return _activity(ACTION_VIEW, uri=f"geo:0,0?q={quote(query, safe=',')}")


def dial_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Open, but never automatically call, a phone number."""
    number = data["phone_number"].strip()
    if not number or any(char not in "+0123456789()-. " for char in number):
        raise vol.Invalid("Enter a valid phone number")
    return _activity(ACTION_DIAL, uri=f"tel:{quote(number, safe='+')}")


def sms_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Open the SMS composition UI."""
    recipient = data.get("recipient", "").strip()
    uri = f"smsto:{quote(recipient, safe='+;,')}"
    if message := data.get("message", ""):
        uri += f"?{urlencode({'body': message})}"
    return _activity(ACTION_SENDTO, uri=uri)


def email_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Open an email-capable app using a mailto URI."""
    recipients = data.get("to", [])
    if isinstance(recipients, str):
        recipients = [recipients]
    params = {}
    for key in ("cc", "bcc"):
        value = data.get(key, [])
        if isinstance(value, str):
            value = [value]
        if value:
            params[key] = ",".join(value)
    for key in ("subject", "body"):
        if data.get(key):
            params[key] = data[key]
    uri = f"mailto:{quote(','.join(recipients), safe='@,+')}"
    if params:
        uri += f"?{urlencode(params)}"
    return _activity(ACTION_SENDTO, uri=uri)


def _milliseconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return int(value.timestamp() * 1000)


def calendar_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Open Android's calendar event insertion UI."""
    start: datetime = data["start"]
    end: datetime = data["end"]
    all_day = data.get("all_day", False)
    if all_day:
        if end.date() <= start.date():
            raise vol.Invalid("All-day event end date must be after start date")
        start = datetime.combine(start.date(), time.min, UTC)
        end = datetime.combine(end.date(), time.min, UTC)
    elif end <= start:
        raise vol.Invalid("Event end must be after start")
    items: list[dict[str, Any]] = [
        {"name": "title", "type": "string", "value": data["title"]},
        {"name": "beginTime", "type": "long", "value": _milliseconds(start)},
        {"name": "endTime", "type": "long", "value": _milliseconds(end)},
    ]
    mapping = {
        "location": "eventLocation",
        "description": "description",
        "attendees": "android.intent.extra.EMAIL",
    }
    for field, name in mapping.items():
        value = data.get(field)
        if value:
            if isinstance(value, list):
                value = ",".join(value)
            items.append({"name": name, "type": "string", "value": value})
    if all_day:
        items.append({"name": "allDay", "type": "boolean", "value": True})
    return _activity(
        ACTION_INSERT,
        uri="content://com.android.calendar/events",
        extras=serialize_extras(items),
    )


def web_search_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Use Android's generic web-search intent."""
    extras = serialize_extras(
        [{"name": "query", "type": "string", "value": data["query"]}]
    )
    return _activity(ACTION_WEB_SEARCH, extras=extras)


def settings_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Open a curated public Android Settings action."""
    return _activity(SETTINGS[data["page"]][0])


def app_settings_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Open a curated settings page scoped to an application."""
    package = resolve_app(data)
    action, _api, target = APP_SETTINGS[data["page"]]
    if target == "extra":
        extras = serialize_extras(
            [
                {
                    "name": "android.provider.extra.APP_PACKAGE",
                    "type": "string",
                    "value": package,
                }
            ]
        )
        return _activity(action, extras=extras)
    return _activity(action, uri=f"package:{package}")


def camera_payload(*, video: bool = False) -> dict[str, Any]:
    """Open the camera UI without promising an activity result."""
    return _activity(ACTION_VIDEO_CAPTURE if video else ACTION_IMAGE_CAPTURE)
