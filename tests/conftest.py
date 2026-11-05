"""Initialize Home Assistant before its validation-library compatibility alias."""

# Current Home Assistant installs its validator as voluptuous during import.
# Match server startup before test modules retain references to validator errors.
import ast
import runpy
from pathlib import Path
from types import SimpleNamespace

import homeassistant
import pytest


@pytest.fixture
def mobile_app_helpers():
    """Load actual pure helpers without optional cloud/Assist dependencies."""
    root = Path(homeassistant.__file__).parent / "components" / "mobile_app"
    namespace = runpy.run_path(str(root / "const.py"))
    tree = ast.parse((root / "util.py").read_text(encoding="utf-8"))
    names = {"webhook_id_from_device_id", "supports_push", "get_notify_service"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(functions) == len(names)
    for function in functions:
        function.decorator_list = []
    header = ast.parse("from __future__ import annotations").body
    module = ast.Module(body=header + functions, type_ignores=[])
    exec(compile(module, str(root / "util.py"), "exec"), namespace)  # noqa: S102
    return SimpleNamespace(**{name: namespace[name] for name in names})
