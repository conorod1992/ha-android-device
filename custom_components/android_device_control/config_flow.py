"""Config flow for Android Device Control."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from .const import DOMAIN


class AndroidDeviceControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single service-only integration entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm setup; no manual device mapping is required."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Android Device Control", data={})
        return self.async_show_form(step_id="user")
