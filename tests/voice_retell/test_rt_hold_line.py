"""The Retell hold line (voice/retell/hold_line.py): it only ever says its fixed lines,
runs on the model config/models.yaml names, and points only its own number at itself."""

from voice.retell import hold_line as hold


def test_it_opens_with_the_hold_line_and_never_waits_for_the_caller():
    body = hold.llm_body()
    assert body["begin_message"] == hold.FIRST and body["start_speaker"] == "agent"


def test_the_prompt_holds_every_line_it_may_say_word_for_word():
    for line in (hold.AGAIN, hold.GOODBYE, hold.ASIDE):
        assert line in hold.PROMPT


def test_its_model_comes_from_the_models_config():
    assert hold.llm_body()["model"] == "claude-5.5-haiku"


def test_it_repeats_the_hold_line_for_about_six_minutes_then_hangs_up():
    body = hold.agent_body("llm_x", "voice_x")
    assert body["reminder_trigger_ms"] * body["reminder_max_count"] <= body["end_call_after_silence_ms"]
    assert body["end_call_after_silence_ms"] == 6 * 60 * 1000


def test_it_never_touches_the_demo_lines_or_the_other_projects_number():
    assert hold.NUMBER not in {"+15162129051", "+16466240418", "+16787376865"}


def test_it_stays_quiet_on_chatter_and_waits_at_least_45_seconds_between_reminders():
    """call_ac439f0ed2852a993282281c66c: every "okay" and "you're welcome" got the hold line back,
    five times in 22 seconds. Chatter now gets Retell's silent reply."""
    assert f"answer exactly {hold.SILENT}" in hold.PROMPT
    assert hold.SILENT == "NO_RESPONSE_NEEDED"
    assert hold.agent_body("llm_x", "voice_x")["reminder_trigger_ms"] >= 45_000
