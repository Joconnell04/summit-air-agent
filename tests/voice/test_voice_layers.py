"""Deleting the losing voice folder after the bake-off must be clean (docs/architecture.md):
nothing outside voice/ imports from it."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED = ["app", "prompts", "servicetitan_mock", "evals", "scripts", "tenants"]


def test_no_shared_code_imports_a_voice_layer():
    pattern = re.compile(r"^\s*(from|import)\s+voice\b", re.MULTILINE)
    for folder in SHARED:
        for path in (ROOT / folder).rglob("*.py"):
            assert not pattern.search(path.read_text()), path
