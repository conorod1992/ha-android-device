"""Keep translated action forms and flat service inputs consistent."""

import json
from pathlib import Path
from types import SimpleNamespace

import voluptuous as vol
from homeassistant.helpers import service as service_helpers
from homeassistant.util.yaml import load_yaml

from custom_components.android_device_control import services as module
from custom_components.android_device_control.const import DOMAIN

from .test_services import FakeBus, FakeServices

ROOT = Path("custom_components/android_device_control")


def test_english_translation_matches_source_semantically():
    source = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads((ROOT / "translations/en.json").read_text(encoding="utf-8"))
    assert source == english


def field_names(fields, translated):
    result = set()
    for name, metadata in fields.items():
        if "fields" in metadata:
            assert metadata["collapsed"] is True
            assert translated["sections"][name]["name"]
            result.update(field_names(metadata["fields"], translated))
        else:
            assert translated["fields"][name]["name"]
            assert translated["fields"][name]["description"]
            result.add(name)
    return result


def schema_names(schema):
    if isinstance(schema, vol.Any):
        return set().union(*(schema_names(item) for item in schema.validators))
    return {getattr(key, "schema", key) for key in schema.schema}


async def test_ui_sections_preserve_flat_registered_fields():
    hass = SimpleNamespace(data={}, services=FakeServices(), bus=FakeBus())
    module.async_register_services(hass)
    try:
        metadata = load_yaml(ROOT / "services.yaml")
        service_helpers._SERVICES_SCHEMA(metadata)
        strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
        assert set(metadata) == set(hass.services.schemas)
        for name, description in metadata.items():
            names = field_names(description["fields"], strings["services"][name])
            assert names <= schema_names(hass.services.schemas[name]), name
        fields = metadata["ask_choice"]["fields"]
        assert list(fields).index("choice_1_label") < list(fields).index(
            "advanced_choices"
        )
        assert "choices" in fields["advanced_choices"]["fields"]
        validated = hass.services.schemas["ask_choice"](
            {
                "device_id": "phone",
                "title": "Destination",
                "message": "Where should I go?",
                "choice_1_label": "Home",
                "choice_1_id": "home",
            }
        )
        assert validated["choice_1_id"] == "home"
        assert "advanced_choices" not in validated
    finally:
        await module.async_unregister_services(hass)
    assert DOMAIN not in hass.data
