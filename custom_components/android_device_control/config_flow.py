"""Config flow for Android Device Control."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from .const import DOMAIN
from .device import discover_android_devices


class AndroidDeviceControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single service-only integration entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Discover Android registrations and explain the service-only setup."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Android Device Control", data={})
        devices = discover_android_devices(self.hass)
        if not devices:
            return self.async_show_form(step_id="no_devices")
        return self.async_show_form(
            step_id="user",
            description_placeholders={
                "device_count": str(len(devices)),
                "device_names": ", ".join(device["device_name"] for device in devices),
                "ready_count": str(sum(device["ready"] for device in devices)),
            },
        )

    async def async_step_no_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Allow setup while explaining how to add a compatible device later."""
        if user_input is not None:
            return self.async_create_entry(title="Android Device Control", data={})
        return self.async_show_form(step_id="no_devices")
