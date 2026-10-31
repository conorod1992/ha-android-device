"""Initialize Home Assistant before its validation-library compatibility alias."""

# Current Home Assistant installs its validator as voluptuous during import.
# Match server startup before test modules retain references to validator errors.
import homeassistant  # noqa: F401
