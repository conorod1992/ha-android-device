"""Curated Android applications shared by friendly actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import voluptuous as vol


@dataclass(frozen=True, slots=True)
class AndroidApp:
    """A canonical Android package and the capabilities we expose for it."""

    name: str
    capabilities: frozenset[str]


APP_REGISTRY: Final[dict[str, AndroidApp]] = {
    "io.homeassistant.companion.android": AndroidApp(
        "Home Assistant", frozenset({"launch"})
    ),
    "com.android.chrome": AndroidApp("Google Chrome", frozenset({"launch", "browser"})),
    "com.google.android.apps.maps": AndroidApp(
        "Google Maps", frozenset({"launch", "maps", "navigation"})
    ),
    "com.google.android.deskclock": AndroidApp(
        "Google Clock", frozenset({"launch", "clock"})
    ),
    "com.google.android.gm": AndroidApp("Gmail", frozenset({"launch", "email"})),
    "com.google.android.calendar": AndroidApp(
        "Google Calendar", frozenset({"launch", "calendar"})
    ),
    "com.google.android.apps.photos": AndroidApp(
        "Google Photos", frozenset({"launch"})
    ),
    "com.google.android.youtube": AndroidApp("YouTube", frozenset({"launch", "media"})),
    "com.google.android.apps.youtube.music": AndroidApp(
        "YouTube Music", frozenset({"launch", "media"})
    ),
    "com.spotify.music": AndroidApp("Spotify", frozenset({"launch", "media"})),
    "com.netflix.mediaclient": AndroidApp("Netflix", frozenset({"launch", "media"})),
    "com.plexapp.android": AndroidApp("Plex", frozenset({"launch", "media"})),
    "com.whatsapp": AndroidApp("WhatsApp", frozenset({"launch", "messaging"})),
    "com.facebook.katana": AndroidApp("Facebook", frozenset({"launch"})),
    "com.facebook.orca": AndroidApp(
        "Facebook Messenger", frozenset({"launch", "messaging"})
    ),
    "com.instagram.android": AndroidApp("Instagram", frozenset({"launch"})),
    "com.reddit.frontpage": AndroidApp("Reddit", frozenset({"launch"})),
    "com.discord": AndroidApp("Discord", frozenset({"launch", "messaging"})),
    "org.telegram.messenger": AndroidApp(
        "Telegram", frozenset({"launch", "messaging"})
    ),
    "com.microsoft.teams": AndroidApp(
        "Microsoft Teams", frozenset({"launch", "messaging"})
    ),
    "com.microsoft.office.outlook": AndroidApp(
        "Microsoft Outlook", frozenset({"launch", "email", "calendar"})
    ),
    "com.microsoft.emmx": AndroidApp(
        "Microsoft Edge", frozenset({"launch", "browser"})
    ),
    "com.waze": AndroidApp("Waze", frozenset({"launch", "maps", "navigation"})),
    "com.amazon.avod.thirdpartyclient": AndroidApp(
        "Amazon Prime Video", frozenset({"launch", "media"})
    ),
    "com.disney.disneyplus": AndroidApp("Disney+", frozenset({"launch", "media"})),
    "org.mozilla.firefox": AndroidApp("Firefox", frozenset({"launch", "browser"})),
    "org.videolan.vlc": AndroidApp("VLC", frozenset({"launch", "media"})),
    "com.google.android.apps.docs": AndroidApp("Google Drive", frozenset({"launch"})),
    "com.google.android.keep": AndroidApp("Google Keep", frozenset({"launch"})),
    "com.google.android.apps.messaging": AndroidApp(
        "Google Messages", frozenset({"launch", "messaging"})
    ),
    "com.google.android.dialer": AndroidApp("Google Phone", frozenset({"launch"})),
}

# Backwards-compatible public mapping used by existing tests and automations.
COMMON_APPS: Final = {package: app.name for package, app in APP_REGISTRY.items()}


def packages_for(capability: str) -> set[str]:
    """Return curated packages that advertise a capability."""
    return {
        package
        for package, app in APP_REGISTRY.items()
        if capability in app.capabilities
    }


def resolve_app(data: dict, *, capability: str = "launch") -> str:
    """Resolve a curated selection or backwards-compatible custom package."""
    app = data.get("app")
    package_name = data.get("package_name", "").strip()
    if app == "custom":
        if not package_name:
            raise vol.Invalid("Package ID is required for Custom package")
        return package_name
    if app:
        if package_name:
            raise vol.Invalid("Do not set Package ID when a common app is selected")
        if app not in packages_for(capability):
            raise vol.Invalid(f"Selected app does not support {capability}")
        return app
    if package_name:
        return package_name
    raise vol.Invalid("Select a common app or provide a Package ID")
