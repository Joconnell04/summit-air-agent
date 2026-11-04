"""The design system's export for Claude (scripts/export_design_system.py) stays in step with the
stylesheet it is built from, so the saved copy never drifts from the app."""

import re

from scripts import export_design_system as export

TOKEN_FAMILIES = ("color", "spacing", "radius", "shadow", "size", "text", "weight")


def exported_names(tokens: dict) -> set[str]:
    return {t["name"] for fam in TOKEN_FAMILIES for t in tokens[fam]["tokens"]}


def test_every_token_in_the_stylesheet_is_exported_with_its_value_and_a_usage_note():
    values = export.read_values()
    tokens = export.build_tokens("main@test")
    names = exported_names(tokens)
    assert set(values) - names == export.ALIASES, "a new token in tokens.css needs a usage note"
    for fam in TOKEN_FAMILIES:
        for t in tokens[fam]["tokens"]:
            assert t["usage"], t["name"]
            assert t["value"].lower() == values[t["name"]].lower(), t["name"]


def test_the_names_only_the_stylesheet_holds_are_declared_before_it():
    for name in export.ALIASES:
        assert re.search(rf"--{name}:", export.BUNDLE_HEAD), name
    assert "--font: var(--font-sans)" in export.BUNDLE_HEAD
    assert set(export.build_tokens("main@test")["type"]["families"]) == {"sans", "mono"}


def test_color_values_are_in_a_shape_a_claude_design_system_reads():
    for t in export.build_tokens("main@test")["color"]["tokens"]:
        assert re.fullmatch(r"#[0-9a-f]{6}", t["value"]), t


def test_the_compiled_tokens_declare_every_exported_token():
    tokens = export.build_tokens("main@test")
    css = export.compiled_css(tokens)
    for name in exported_names(tokens):
        assert f"--{name}:" in css, name


def test_every_component_has_its_guidelines_in_the_design_system_doc():
    sections = export.doc_sections()
    names = [c[0] for c in export.COMPONENTS]
    assert len(names) == len(set(names))
    for name, *_, docs, _classes, _provides in export.COMPONENTS:
        for heading in docs:
            assert heading in sections, f"{name}: no '### {heading}' in docs/design-system.md"


def test_the_brand_book_has_no_em_dashes_and_starts_after_its_title():
    rules = (export.REPO / "docs/design-system-rules.md").read_text()
    assert chr(0x2014) not in rules
    book = rules.split("\n\n", 2)[2]
    assert book.startswith("Build every owner view page")
