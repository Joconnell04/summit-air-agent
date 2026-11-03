"""The hash a results file carries: the prompts, the tenant folders, the scenarios it ran and the
model profiles.

The ratchet recomputes it offline, so a committed run goes stale the moment any of these change.
"""
import hashlib
import json
from pathlib import Path

from evals.models import load_config, profile
from evals.scenario import SCENARIOS_DIR, scenario_path
from evals.stacks import PROMPTS_DIR, TENANTS_DIR, prompt_files

EVAL_PROMPTS = PROMPTS_DIR / "evals"
# Tenant wording, rules and seed data shape every call as much as the prompts do. Audio clips
# are left out; they are rendered from wording that is already hashed.
TENANT_SUFFIXES = {".yaml", ".md", ".py"}


def tenant_files(tenants_dir: Path = TENANTS_DIR) -> list[Path]:
    return sorted(
        p for p in tenants_dir.rglob("*")
        if p.is_file() and p.suffix in TENANT_SUFFIXES and "__pycache__" not in p.parts
    )


def prompt_hash(prompts_dir: Path = PROMPTS_DIR) -> str:
    """The agent's prompt files alone (and the eval prompts), so a LangSmith experiment says
    whether the prompt changed between two runs."""
    digest = hashlib.sha256()
    for path in prompt_files(prompts_dir) + sorted((prompts_dir / "evals").glob("*.md")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def run_hash(
    scenario_ids: list[str],
    profile_name: str,
    scenarios_dir: Path = SCENARIOS_DIR,
    prompts_dir: Path = PROMPTS_DIR,
    config: dict | None = None,
    tenants_dir: Path = TENANTS_DIR,
) -> str:
    config = config or load_config()
    digest = hashlib.sha256()
    files = prompt_files(prompts_dir) + sorted((prompts_dir / "evals").glob("*.md"))
    files += [scenario_path(i, scenarios_dir) for i in sorted(set(scenario_ids))]
    for path in files:
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    for path in tenant_files(tenants_dir):
        rel = path.relative_to(tenants_dir).as_posix()
        digest.update(rel.encode() + b"\0" + path.read_bytes() + b"\0")
    profiles = {
        name: profile(name, config) for name in (profile_name, "offline.caller", "offline.grader")
    }
    digest.update(json.dumps(profiles, sort_keys=True).encode())
    return digest.hexdigest()[:16]
