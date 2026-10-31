"""Keep complete user recipes valid for Home Assistant's script parser."""

import re
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.config_validation import SCRIPT_SCHEMA
from homeassistant.util.yaml import parse_yaml


async def test_notification_recipes_parse_as_home_assistant_actions(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    source = Path("docs/NOTIFICATION_EXAMPLES.md").read_text(encoding="utf-8")
    fence = chr(96) * 3
    examples = re.findall(fence + r"yaml\n(.*?)" + fence, source, re.DOTALL)
    assert len(examples) == 3
    try:
        for example in examples:
            data = parse_yaml(example)
            if "sequence" in data:
                SCRIPT_SCHEMA(data["sequence"])
            elif "actions" in data:
                SCRIPT_SCHEMA(data["actions"])
            else:
                SCRIPT_SCHEMA([data])
    finally:
        hass.import_executor.shutdown(wait=False)
