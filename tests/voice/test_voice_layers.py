"""Deleting the losing voice folder after the bake-off must be clean (docs/architecture.md):
nothing outside voice/ imports the LiveKit layer or the Retell layer."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHARED = ["app", "prompts", "servicetitan_mock", "evals", "scripts", "tenants"]
LAYERS = ["livekit", "retell"]


def import_pattern(layer: str) -> re.Pattern:
    """An import statement that reaches into voice/<layer>, in any of its spellings, or a dynamic
    import of it by a literal name."""
    return re.compile(
        rf"^\s*(?:from\s+voice(?:\.{layer}\b|\s+import\s+[^\n]*\b{layer}\b)"
        rf"|import\s+voice\.{layer}\b)"
        rf"|(?:import_module|__import__)\(\s*[\"']voice\.{layer}\b",
        re.MULTILINE,
    )


@pytest.mark.parametrize("layer", LAYERS)
def test_no_shared_code_imports_a_voice_layer(layer):
    pattern = import_pattern(layer)
    for folder in SHARED:
        for path in (ROOT / folder).rglob("*.py"):
            assert not pattern.search(path.read_text()), f"{path} imports voice.{layer}"


@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize(
    "line",
    [
        "from voice.{layer} import chat",
        "from voice.{layer}.chat import open_chat",
        "    from voice.{layer}.chat import open_chat",
        "import voice.{layer}",
        "import voice.{layer}.chat as chat",
        "from voice import {layer}",
        "from voice import bridge, {layer}",
        'importlib.import_module("voice.{layer}.chat")',
    ],
)
def test_the_boundary_check_catches_every_way_of_importing_a_layer(layer, line):
    assert import_pattern(layer).search(line.format(layer=layer))


@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize(
    "line",
    [
        "import voice",
        "from voice import other",
        "from voice.other import x",
        "# see voice/{layer}/README.md",
        'importlib.import_module(f"voice.{{stack}}.eval_adapter")',
    ],
)
def test_the_boundary_check_leaves_alone_what_is_not_an_import_of_that_layer(layer, line):
    assert not import_pattern(layer).search(line.format(layer=layer))
