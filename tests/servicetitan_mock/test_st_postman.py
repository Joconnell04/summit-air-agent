"""The Postman collection must cover every endpoint, and must match its generator."""

import importlib.util
import json
import re

from conftest import ROOT, TENANT_DIR

from servicetitan_mock.app import create_app

spec = importlib.util.spec_from_file_location(
    "build_postman_st", ROOT / "scripts" / "build_postman_st.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

COLLECTION = ROOT / "postman" / "servicetitan-mock.postman_collection.json"
# /debug/reset wipes the world and exists only for the eval instance (tests/servicetitan_mock/
# test_st_reset.py), so a collection that runs against any mock must not call it.
SKIPPED = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/debug/reset"}


def normalize(path: str) -> str:
    """A path with its variables and literal ids (job-types/203) read as {}."""
    path = re.sub(r"\{\{?[^}]+\}\}?", "{}", path.split("?")[0].removeprefix("{{base_url}}"))
    return re.sub(r"/\d+(?=/|$)", "/{}", path)


def collection_requests(items) -> set[tuple[str, str]]:
    found = set()
    for entry in items:
        if "item" in entry:
            found |= collection_requests(entry["item"])
        else:
            found.add((entry["request"]["method"], normalize(entry["request"]["url"])))
    return found


def test_the_collection_has_a_request_for_every_endpoint(tmp_path):
    # Read from the OpenAPI paths: from FastAPI 0.16x, app.routes holds included routers as one
    # entry each, so a walk of it saw only the app's own routes and passed whatever was missing.
    app = create_app(tmp_path / "x.db", TENANT_DIR, tmp_path / "x.log")
    endpoints = {
        (method.upper(), normalize(path))
        for path, methods in app.openapi()["paths"].items()
        if path not in SKIPPED
        for method in methods
    }
    assert len(endpoints) > 40
    covered = collection_requests(json.loads(COLLECTION.read_text())["item"])
    assert endpoints - covered == set()


def test_every_request_has_assertions():
    def walk(items):
        for entry in items:
            if "item" in entry:
                yield from walk(entry["item"])
            else:
                yield entry

    for entry in walk(json.loads(COLLECTION.read_text())["item"]):
        script = "\n".join(entry["event"][-1]["script"]["exec"])
        assert "pm.test(" in script, entry["name"]


def test_the_committed_files_match_the_generator():
    assert json.loads(COLLECTION.read_text()) == builder.build()
    environment = ROOT / "postman" / "servicetitan-mock.postman_environment.json"
    assert json.loads(environment.read_text()) == builder.environment()
