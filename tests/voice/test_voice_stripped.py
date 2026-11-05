"""Jackson's "our stack, stripped" (voice.profile: stripped): a one-page prompt, no speech filter,
no owner guidance or stage notes, no side model; the tool server and its gates unchanged."""

import asyncio
from pathlib import Path

from livekit.agents import Agent

from app import side_model
from app.tenant import load_agent
from prompts import assemble
from tests.voice.test_voice_call import ENDPOINTING, Session, _stream
from voice.livekit.call import Call, SummitAgent
from voice.livekit.settings import load_voice
from voice.livekit.tools import ToolLog
from voice.livekit.worker import agent_instructions, is_stripped

TENANT = Path(__file__).resolve().parents[2] / "tenants" / "summit-air"
START = {
    "tenant": {"slug": "summit-air", "name": "Summit Air", "timezone": "America/New_York"},
    "knowledge": {"faq": "We work in all five boroughs."},
    "customers": [],
    "house_rules": {"guidance": {"opening": ["Always mention our spring special."]}},
}


def test_the_switch_is_off_by_default():
    assert load_voice()["profile"] == "full"
    assert not is_stripped(load_voice())
    assert is_stripped({"profile": "stripped"})


def test_the_stripped_prompt_is_one_page_with_the_safety_lines_word_for_word():
    text = agent_instructions({"profile": "stripped"}, START, TENANT)
    page = (assemble.PROMPTS / assemble.STRIPPED).read_text()
    assert 400 <= len(page.split()) <= 600
    safety = load_agent(TENANT)[0]["safety"]
    for spec in safety["kinds"].values():
        assert " ".join(spec["lines"].split()) in text
    assert " ".join(safety["sign_off"].split()) in text
    assert "Summit Air" in text and "{company}" not in text
    assert "We work in all five boroughs." in text, "the company knowledge stays"
    assert "spring special" not in text, "no owner guidance"
    full = agent_instructions({"profile": "full"}, START, TENANT)
    assert "spring special" in full and len(full) > 3 * len(text)


def test_owner_guidance_and_stage_notes_are_left_out():
    hints = ["stage_booking", "guidance:Offer the membership.", "pace_slower", "units:1,2"]
    assert assemble.without_guidance(hints) == ["pace_slower", "units:1,2"]


def test_the_model_text_is_spoken_unfiltered(monkeypatch):
    """The speech filter would drop the restated ZIP and the follow-up rule would ask again."""
    monkeypatch.setattr(
        Agent.default, "llm_node", _stream("Got it, 3221 Avenue N, Brooklyn, 11234. ", "Sure.")
    )
    call = Call(None, None, ToolLog(), ENDPOINTING, stripped=True)
    call.session = Session()
    call.reply.heard(None, "it's three two two one avenue n brooklyn one one two three four")
    agent = SummitAgent(call, "", [])

    async def run():
        out = []
        async for chunk in agent.llm_node(None, [], None):
            out.append(chunk.delta.content if hasattr(chunk, "delta") and chunk.delta else chunk)
        return "".join(c or "" for c in out)

    assert asyncio.run(run()) == "Got it, 3221 Avenue N, Brooklyn, 11234. Sure."
    assert call.session.replies == []


def test_the_side_model_is_off(tmp_path):
    config = tmp_path / "models.yaml"
    config.write_text("voice: {profile: stripped}\n")
    assert side_model.stripped_profile(config)
    assert side_model.from_settings("openai-decisions", stripped=True) is None
