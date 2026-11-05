"""The simulated caller: a cheap model (profile offline.caller) playing the scenario's persona."""
from pathlib import Path

import yaml

from evals.models import Model, Usage
from evals.scenario import Scenario

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "evals" / "caller.md"
PERSONAS = Path(__file__).resolve().parent / "personas"
END = "[END]"


def load_style(name: str | None, directory: Path = PERSONAS) -> dict:
    """A caller style (evals/personas/README.md), or an empty one when the scenario sets none."""
    if not name:
        return {}
    return yaml.safe_load((directory / f"{name}.yaml").read_text())


def system_prompt(scenario: Scenario, extra_facts: dict | None = None) -> str:
    facts = {**scenario.hidden_facts, **(extra_facts or {})}
    style = load_style(scenario.caller_style)
    persona = scenario.persona.strip()
    script = [f"- {line}" for line in scenario.script]
    if style:
        # The style rides in the persona and script slots, so the caller prompt itself (and the
        # hash of every run that doesn't use a style) is unchanged.
        persona += "\n\nHow you talk: " + " ".join(style["manner"].split())
        examples = "; ".join(f'"{line}"' for line in style.get("lines", []))
        script.append(f"- Lines you have said on calls like this, to use where they fit, never as "
                      f"a script: {examples}")
    return PROMPT.read_text().format(
        persona=persona,
        hidden_facts=yaml.safe_dump(facts, sort_keys=False, allow_unicode=True).strip() or "none",
        script="\n".join(script) or "- Behave naturally.",
        traits=", ".join(scenario.traits) or "none",
    )


class SimulatedCaller:
    """Keeps the call from the caller's side: the agent's lines arrive as the other party's."""

    def __init__(self, scenario: Scenario, model: Model, extra_facts: dict | None = None):
        self.model = model
        self.system = system_prompt(scenario, extra_facts)
        self.history: list[dict] = []
        self.usage = Usage()
        self.opening = scenario.opening

    def first_line(self, greeting: str) -> str:
        """The scripted opening, said after the agent's greeting."""
        self.history.append({"role": "user", "content": greeting or "(The phone is answered.)"})
        return self.opening

    def reply(self, agent_text: str, said: str) -> str | None:
        """The caller's next line after the agent's, or None when the caller hangs up. `said` is
        the caller's previous line as the caller meant it, before transcription."""
        self.history.append({"role": "assistant", "content": said})
        self.history.append({"role": "user", "content": agent_text or "(silence)"})
        reply = self.model.complete(self.system, self.history, max_tokens=300)
        self.usage.add(reply.usage)
        text = reply.text.strip()
        if not text or END in text:
            return None
        return text
