"""Regression tests for notification action polish."""

from pathlib import Path

import yaml

from custom_components.android_device_control.notifications import (
    live_update_payload,
    progress_notification_payload,
)


def test_progress_minus_one_removes_progress_bar() -> None:
    """Companion's documented -1 completion value should pass through unchanged."""
    result = progress_notification_payload(
        {
            "message": "Copy complete",
            "tag": "copy",
            "current": -1,
            "indeterminate": False,
        }
    )

    assert result["data"] == {"tag": "copy", "progress": -1}


def test_live_update_presentation_fields_are_forwarded() -> None:
    """Live Updates should retain the shared channel and importance controls."""
    result = live_update_payload(
        {
            "title": "Washer",
            "message": "Rinsing",
            "tag": "washer",
            "channel": "Laundry",
            "importance": "high",
        }
    )

    assert result["data"]["channel"] == "Laundry"
    assert result["data"]["importance"] == "high"
    assert result["data"]["live_update"] is True


def test_service_metadata_exposes_notification_polish() -> None:
    """Action UI metadata should match the runtime notification capabilities."""
    services = yaml.safe_load(
        Path("custom_components/android_device_control/services.yaml").read_text()
    )

    urgent = services["notify_urgent"]["fields"]
    assert urgent["channel"]["default"] == "Urgent"
    assert urgent["importance"]["default"] == "high"
    assert urgent["importance"]["selector"]["select"]["options"] == [
        "default",
        "high",
        "max",
    ]

    progress = services["notify_progress"]["fields"]
    assert progress["current"]["selector"]["number"]["min"] == -1

    live_update = services["notify_live_update"]["fields"]
    assert "channel" in live_update
    assert "importance" in live_update
