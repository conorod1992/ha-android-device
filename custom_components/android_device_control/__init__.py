"""Android Device Control integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .services import async_register_services, async_unregister_services


async def async_setup_entry(hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Set up Android Device Control from a config entry."""
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Unload Android Device Control."""
    async_unregister_services(hass)
    return True
