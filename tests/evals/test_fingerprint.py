import shutil

from app.tenant import TRADES_DIR
from evals.fingerprint import run_hash
from evals.stacks import PROMPTS_DIR, TENANTS_DIR


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


def test_a_trade_edit_makes_the_run_stale(tmp_path):
    trades = tmp_path / "trades"
    shutil.copytree(TRADES_DIR, trades)
    before = run_hash([], "fast", trades_dir=trades)
    agent = trades / "hvac" / "agent.yaml"
    agent.write_text(agent.read_text().replace("- sulfur", "- sulphur"))
    assert run_hash([], "fast", trades_dir=trades) != before


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


def test_a_prompt_that_is_not_part_of_the_call_leaves_the_run_current(tmp_path):
    # The technician summary is written after the call (app/summary.py); its prompt sits beside the
    # agent's, but changing it says nothing about how the voice agent behaves.
    prompts = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, prompts, ignore=shutil.ignore_patterns("__pycache__"))
    before = run_hash([], "fast", prompts_dir=prompts)
    (prompts / "post-call-summary.md").write_text("Summarise the call for the technician.")
    assert run_hash([], "fast", prompts_dir=prompts) == before
    spine = prompts / "spine.md"
    spine.write_text(spine.read_text() + "\nAn edit.\n")
    assert run_hash([], "fast", prompts_dir=prompts) != before
