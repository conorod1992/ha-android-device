"""Tests for friendly standard Android intent builders."""

from datetime import UTC, datetime

import pytest
import voluptuous as vol

from custom_components.android_device_control.intents import (
    APP_SETTINGS,
    SETTINGS,
    app_settings_payload,
    calendar_payload,
    dial_payload,
    email_payload,
    navigate_payload,
    open_url_payload,
    serialize_extras,
    settings_payload,
    show_map_payload,
    sms_payload,
    web_search_payload,
)


def data_of(payload: dict) -> dict:
    return payload["data"]


def test_open_url_preserves_encoded_https_query_and_unicode() -> None:
    result = data_of(
        open_url_payload({"url": "https://example.test/a?x=caf%C3%A9&y=1"})
    )
    assert result["intent_action"] == "android.intent.action.VIEW"
    assert result["intent_uri"].endswith("x=caf%C3%A9&y=1")
    assert "intent_package_name" not in result


@pytest.mark.parametrize("url", ["example.test", "https://example.test/a b", ""])
def test_open_url_rejects_invalid_absolute_uri(url: str) -> None:
    with pytest.raises(vol.Invalid):
        open_url_payload({"url": url})


def test_open_url_can_target_curated_browser() -> None:
    result = data_of(
        open_url_payload({"url": "https://example.test", "app": "org.mozilla.firefox"})
    )
    assert result["intent_package_name"] == "org.mozilla.firefox"


def test_show_map_address_and_coordinates_are_encoded() -> None:
    address = data_of(show_map_payload({"location": "St Stephen's Green, Dublin"}))
    assert address["intent_uri"] == "geo:0,0?q=St%20Stephen%27s%20Green,%20Dublin"
    coordinates = data_of(
        show_map_payload(
            {"latitude": 53.3498, "longitude": -6.2603, "label": "Dublin café"}
        )
    )
    assert coordinates["intent_uri"].startswith("geo:0,0?q=53.3498,-6.2603")
    assert "%C3%A9" in coordinates["intent_uri"]


def test_show_map_google_and_waze_providers() -> None:
    google = data_of(
        show_map_payload({"location": "Dublin", "provider": "google_maps"})
    )
    assert google["intent_package_name"] == "com.google.android.apps.maps"
    waze = data_of(show_map_payload({"location": "Dublin", "provider": "waze"}))
    assert waze["intent_uri"] == "https://waze.com/ul?q=Dublin"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"latitude": 1},
        {"latitude": 91, "longitude": 0},
        {"location": "Dublin", "latitude": 1, "longitude": 2},
    ],
)
def test_location_rejects_invalid_combinations(data: dict) -> None:
    with pytest.raises(vol.Invalid):
        show_map_payload(data)


def test_navigation_provider_contracts_and_encoding() -> None:
    default = data_of(navigate_payload({"location": "Dublin Airport"}))
    assert default["intent_uri"] == "geo:0,0?q=Dublin%20Airport"
    google = data_of(
        navigate_payload(
            {
                "location": "Dublin Airport & Terminal 2",
                "provider": "google_maps",
                "travel_mode": "driving",
            }
        )
    )
    assert google["intent_uri"].startswith("https://www.google.com/maps/dir/?api=1")
    assert "destination=Dublin+Airport+%26+Terminal+2" in google["intent_uri"]
    assert "travelmode=driving" in google["intent_uri"]
    waze = data_of(
        navigate_payload({"latitude": 53.4, "longitude": -6.2, "provider": "waze"})
    )
    assert "ll=53.4%2C-6.2" in waze["intent_uri"]
    assert "navigate=yes" in waze["intent_uri"]


def test_navigation_rejects_unsupported_travel_modes() -> None:
    with pytest.raises(vol.Invalid, match="Travel mode"):
        navigate_payload({"location": "Dublin", "travel_mode": "walking"})
    with pytest.raises(vol.Invalid, match="Waze"):
        navigate_payload(
            {"location": "Dublin", "provider": "waze", "travel_mode": "walking"}
        )


def test_phone_sms_and_email_composition() -> None:
    assert (
        data_of(dial_payload({"phone_number": "+353 1 234 5678"}))["intent_uri"]
        == "tel:+353%201%20234%205678"
    )
    sms = data_of(sms_payload({"recipient": "+353123", "message": "Hello, café"}))
    assert sms["intent_uri"] == "smsto:+353123?body=Hello%2C+caf%C3%A9"
    email = data_of(
        email_payload(
            {
                "to": ["a@example.test", "b@example.test"],
                "cc": ["c@example.test"],
                "bcc": [],
                "subject": "Hello & welcome",
                "body": "Line 1\nLine 2",
            }
        )
    )
    assert email["intent_uri"].startswith("mailto:a@example.test,b@example.test?")
    assert "subject=Hello+%26+welcome" in email["intent_uri"]
    assert "body=Line+1%0ALine+2" in email["intent_uri"]


def test_calendar_fields_timezone_all_day_and_attendees() -> None:
    result = data_of(
        calendar_payload(
            {
                "title": "Planning, phase: 2",
                "start": datetime(2026, 7, 27, 9, tzinfo=UTC),
                "end": datetime(2026, 7, 28, 10, tzinfo=UTC),
                "all_day": True,
                "location": "Dublin",
                "description": "Discuss café",
                "attendees": ["a@example.test", "b@example.test"],
            }
        )
    )
    assert result["intent_action"] == "android.intent.action.INSERT"
    assert result["intent_uri"] == "content://com.android.calendar/events"
    assert "allDay:true:boolean" in result["intent_extras"]
    assert "a%40example.test%2Cb%40example.test" in result["intent_extras"]
    assert "beginTime:1785110400000:long" in result["intent_extras"]


def test_calendar_rejects_end_before_start() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(vol.Invalid, match="after start"):
        calendar_payload({"title": "Bad", "start": start, "end": start})


@pytest.mark.parametrize(
    ("kind", "value", "suffix"),
    [
        (
            "string",
            "Hello, café: yes",
            "Hello%2C%20caf%C3%A9%3A%20yes:String.urlencoded",
        ),
        ("boolean", True, "true:boolean"),
        ("integer", 5, "5:int"),
        ("long", 5, "5:long"),
        ("float", 1.5, "1.5:float"),
        ("double", 2.5, "2.5:double"),
        ("integer_list", [1, "2"], "1;2:ArrayList<Integer>"),
    ],
)
def test_every_supported_structured_extra(
    kind: str, value: object, suffix: str
) -> None:
    assert (
        serialize_extras([{"name": "example", "type": kind, "value": value}])
        == f"example:{suffix}"
    )


@pytest.mark.parametrize(
    "item",
    [
        {"name": "", "type": "string", "value": "x"},
        {"name": "bad,name", "type": "string", "value": "x"},
        {"name": "x", "type": "unknown", "value": "x"},
        {"name": "x", "type": "boolean", "value": "true"},
        {"name": "x", "type": "integer_list", "value": ["bad"]},
    ],
)
def test_structured_extras_reject_malformed_values(item: dict) -> None:
    with pytest.raises(vol.Invalid):
        serialize_extras([item])


def test_web_search_uses_generic_android_contract() -> None:
    result = data_of(web_search_payload({"query": "Home Assistant café"}))
    assert result["intent_action"] == "android.intent.action.WEB_SEARCH"
    assert (
        result["intent_extras"]
        == "query:Home%20Assistant%20caf%C3%A9:String.urlencoded"
    )


@pytest.mark.parametrize(("page", "expected"), SETTINGS.items())
def test_every_curated_setting_maps_to_documented_action(
    page: str, expected: tuple
) -> None:
    assert data_of(settings_payload({"page": page}))["intent_action"] == expected[0]


@pytest.mark.parametrize("page", APP_SETTINGS)
def test_every_app_setting_targets_package(page: str) -> None:
    result = data_of(
        app_settings_payload({"page": page, "package_name": "com.example.app"})
    )
    assert (
        result.get("intent_uri") == "package:com.example.app"
        or "com.example.app" in result["intent_extras"]
    )
