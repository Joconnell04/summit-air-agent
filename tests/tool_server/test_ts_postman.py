"""The tool server's Postman collection must cover every endpoint, and must match its generator."""

import importlib.util
import json
import re

from toolkit import ROOT

spec = importlib.util.spec_from_file_location(
    "build_postman_tools", ROOT / "scripts" / "build_postman_tools.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

COLLECTION = ROOT / "postman" / "tool-server.postman_collection.json"
SKIPPED = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def normalize(url: str) -> str:
    path = url.split("?")[0].removeprefix("{{base_url}}").removeprefix("{{st_url}}")
    return re.sub(r"\{\{?[^}]+\}\}?", "{}", path)


def requests(items):
    for entry in items:
        if "item" in entry:
            yield from requests(entry["item"])
        else:
            yield entry


def test_the_collection_has_a_request_for_every_endpoint(agent):
    endpoints = {
        (method, normalize(route.path))
        for route in agent.app.routes
        if hasattr(route, "methods") and route.path not in SKIPPED
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    covered = {
        (e["request"]["method"], normalize(e["request"]["url"]))
        for e in requests(json.loads(COLLECTION.read_text())["item"])
    }
    assert endpoints - covered == set()


def test_every_request_has_assertions_and_the_triage_folder_reads_the_debug_endpoints():
    collection = json.loads(COLLECTION.read_text())
    for entry in requests(collection["item"]):
        script = "\n".join(entry["event"][-1]["script"]["exec"])
        assert "pm.test(" in script, entry["name"]
    triage = next(f for f in collection["item"] if f["name"].endswith("Triage"))
    assert {normalize(e["request"]["url"]) for e in triage["item"]} == {
        "/debug/calls/{}",
        "/debug/logs",
    }


def test_the_committed_files_match_the_generator():
    assert json.loads(COLLECTION.read_text()) == builder.build()
    environment = ROOT / "postman" / "tool-server.postman_environment.json"
    assert json.loads(environment.read_text()) == builder.environment()
