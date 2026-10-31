"""Lifecycle and synchronization helpers for managed actions."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Hashable
from typing import Any
from weakref import WeakValueDictionary

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DATA_CONFIG_ENTRY, DOMAIN


class SessionLocks:
    """Serialize operations for a target without retaining unused target keys."""

    def __init__(self) -> None:
        """Keep locks alive only while an operation holds or awaits them."""
        self._locks: WeakValueDictionary[Hashable, asyncio.Lock] = WeakValueDictionary()

    def get(self, key: Hashable) -> asyncio.Lock:
        """Return the same lock to all overlapping operations for a key."""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def active(self) -> list[asyncio.Lock]:
        """Snapshot operations that shutdown needs to drain."""
        return list(self._locks.values())


def async_create_background_task(
    hass: HomeAssistant, coroutine: Coroutine[Any, Any, None], name: str
) -> asyncio.Task[None]:
    """Create managed work without blocking Home Assistant startup or idle waits."""
    entry = hass.data.get(DOMAIN, {}).get(DATA_CONFIG_ENTRY)
    if entry is not None:
        return entry.async_create_background_task(hass, coroutine, name)
    return hass.async_create_background_task(coroutine, name)


def ensure_running(closing: bool) -> None:
    """Reject an in-flight action once its integration starts unloading."""
    if closing:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="integration_unloading"
        )
