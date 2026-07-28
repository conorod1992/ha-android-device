"""Tests for discovery-based onboarding."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.android_device_control import config_flow as config_flow_module


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch):
    """Return a config flow with unique-ID plumbing isolated."""
    instance = config_flow_module.AndroidDeviceControlConfigFlow()
    instance.hass = SimpleNamespace()
    monkeypatch.setattr(instance, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(instance, "_abort_if_unique_id_configured", Mock())
    return instance


async def test_user_step_reports_discovered_devices(
    monkeypatch: pytest.MonkeyPatch, flow
) -> None:
    monkeypatch.setattr(
        config_flow_module,
        "discover_android_devices",
        lambda _hass: [
            {"device_name": "Pixel 9", "ready": True},
            {"device_name": "Kitchen tablet", "ready": False},
        ],
    )
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["description_placeholders"] == {
        "device_count": "2",
        "device_names": "Pixel 9, Kitchen tablet",
        "ready_count": "1",
    }


async def test_user_step_explains_when_no_devices_are_found(
    monkeypatch: pytest.MonkeyPatch, flow
) -> None:
    monkeypatch.setattr(
        config_flow_module, "discover_android_devices", lambda _hass: []
    )
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "no_devices"


async def test_setup_creates_empty_single_entry(
    monkeypatch: pytest.MonkeyPatch, flow
) -> None:
    monkeypatch.setattr(
        config_flow_module, "discover_android_devices", lambda _hass: []
    )
    result = await flow.async_step_user({})
    assert result["type"] == "create_entry"
    assert result["title"] == "Android Device Control"
    assert result["data"] == {}


async def test_no_device_explanation_can_finish_setup(flow) -> None:
    result = await flow.async_step_no_devices({})
    assert result["type"] == "create_entry"
    assert result["data"] == {}
