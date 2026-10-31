"""Validate intent and notification fields against their receiving contracts."""

from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol
from homeassistant.util import dt as dt_util

from custom_components.android_device_control.commands import raw_payload
from custom_components.android_device_control.intents import (
    calendar_payload,
    email_payload,
    serialize_extras,
)
from custom_components.android_device_control.notifications import (
    progress_notification_payload,
)


@pytest.mark.parametrize(("kind", "bits"), [("integer", 32), ("long", 64)])
@pytest.mark.parametrize("side", [-1, 1])
def test_structured_integer_boundaries(kind, bits, side):
    boundary = -(2 ** (bits - 1)) if side == -1 else 2 ** (bits - 1) - 1
    item = {"name": "value", "type": kind, "value": boundary}
    assert f":{boundary}:" in serialize_extras([item])
    item["value"] += side
    with pytest.raises(vol.Invalid, match="signed"):
        serialize_extras([item])


@pytest.mark.parametrize("kind", ["integer", "long", "integer_list"])
@pytest.mark.parametrize("value", [True, 1.9, float("inf"), float("nan"), "1.9"])
def test_structured_integers_do_not_truncate_or_accept_nonfinite(kind, value):
    item = {
        "name": "value",
        "type": kind,
        "value": [value] if kind == "integer_list" else value,
    }
    with pytest.raises(vol.Invalid):
        serialize_extras([item])


def test_integer_list_uses_android_integer_range():
    with pytest.raises(vol.Invalid, match="signed 32-bit"):
        serialize_extras([{"name": "days", "type": "integer_list", "value": [2**31]}])


@pytest.mark.parametrize("naive_field", ["start", "end"])
def test_calendar_accepts_mixed_timezone_formats(monkeypatch, naive_field):
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Europe/Dublin"))
    data = {
        "title": "Meeting",
        "start": datetime(2026, 7, 1, 9, tzinfo=UTC),
        "end": datetime(2026, 7, 1, 11, tzinfo=UTC),
    }
    data[naive_field] = data[naive_field].replace(tzinfo=None)
    result = calendar_payload(data)
    expected_start = dt_util.as_utc(data["start"])
    expected_end = dt_util.as_utc(data["end"])
    extras = result["data"]["intent_extras"]
    assert f"beginTime:{int(expected_start.timestamp() * 1000)}:long" in extras
    assert f"endTime:{int(expected_end.timestamp() * 1000)}:long" in extras


def test_calendar_compares_instants_across_dst_fold():
    timezone = ZoneInfo("Europe/Dublin")
    result = calendar_payload(
        {
            "title": "Clock change",
            "start": datetime(2026, 10, 25, 1, 45, tzinfo=timezone, fold=0),
            "end": datetime(2026, 10, 25, 1, 15, tzinfo=timezone, fold=1),
        }
    )
    assert "endTime:" in result["data"]["intent_extras"]


def test_calendar_rejects_end_before_normalized_start(monkeypatch):
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Europe/Dublin"))
    with pytest.raises(vol.Invalid, match="after start"):
        calendar_payload(
            {
                "title": "Invalid",
                "start": datetime(2026, 7, 1, 9, tzinfo=UTC),
                "end": datetime(2026, 7, 1, 9, 30),  # noqa: DTZ001 - test local input
            }
        )


def test_mailto_decodes_with_android_uri_semantics():
    result = email_payload(
        {
            "to": ["a+tag@example.test"],
            "subject": "Hello + café",
            "body": "Line 1\nLine 2\rLine 3\r\nLine 4",
        }
    )
    uri = result["data"]["intent_uri"]
    # Android Uri.decode, unlike HTML-form parsing, does not translate + to space.
    headers = {
        name: unquote(value)
        for name, value in (
            part.split("=", 1) for part in urlsplit(uri).query.split("&")
        )
    }
    assert headers["subject"] == "Hello + café"
    assert headers["body"] == "Line 1\r\nLine 2\r\nLine 3\r\nLine 4"
    assert unquote(urlsplit(uri).path) == "a+tag@example.test"


def test_indeterminate_progress_satisfies_companion_render_guard():
    data = progress_notification_payload({"message": "Working", "indeterminate": True})[
        "data"
    ]
    assert data.get("progress", -1) != -1
    assert data["progress_indeterminate"] is True
    assert data["progress_max"] > 0


@pytest.mark.parametrize(
    "command", ["kiosk_reload", "kiosk_show_camera", "kiosk_default"]
)
def test_raw_action_rejects_ios_only_kiosk_commands(command):
    with pytest.raises(vol.Invalid, match="Companion commands"):
        raw_payload(command, {})
