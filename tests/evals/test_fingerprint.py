import shutil

from evals.fingerprint import run_hash
from evals.stacks import TENANTS_DIR


def copy_tenants(tmp_path):
    tenants = tmp_path / "tenants"
    shutil.copytree(TENANTS_DIR, tenants)
    return tenants


def test_a_tenant_wording_edit_makes_the_run_stale(tmp_path):
    tenants = copy_tenants(tmp_path)
    before = run_hash([], "fast", tenants_dir=tenants)
    agent = tenants / "summit-air" / "agent.yaml"
    agent.write_text(agent.read_text() + "\n# an edit\n")
    assert run_hash([], "fast", tenants_dir=tenants) != before


def test_audio_clips_do_not_change_the_hash(tmp_path):
    tenants = copy_tenants(tmp_path)
    before = run_hash([], "fast", tenants_dir=tenants)
    (tenants / "summit-air" / "clips" / "new.wav").write_bytes(b"RIFF")
    assert run_hash([], "fast", tenants_dir=tenants) == before


def test_an_owner_draft_is_part_of_a_gate_runs_hash(tmp_path):
    tenants = copy_tenants(tmp_path)
    plain = run_hash([], "fast", tenants_dir=tenants)
    draft = {"guidance": {"booking": ["Ask about pets."]}, "lines": {}}
    with_draft = run_hash([], "fast", tenants_dir=tenants, overlay=draft)
    assert with_draft != plain
    assert run_hash([], "fast", tenants_dir=tenants, overlay=dict(draft)) == with_draft
    other = {"guidance": {"booking": ["Ask about the dog."]}, "lines": {}}
    assert run_hash([], "fast", tenants_dir=tenants, overlay=other) != with_draft
    assert run_hash([], "fast", tenants_dir=tenants, overlay=None) == plain
