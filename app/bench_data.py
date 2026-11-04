"""What the debug bench (app/bench.py) reads besides the call record: the tuning knobs and model
profiles from config/models.yaml with the measurement each one has in the docs, the server's own
switches, the eval runs in evals/results and the rounds in evals/rounds, and a draft eval scenario
made from a real call. All read-only.
"""

import html
import re
from pathlib import Path

import yaml

from evals import results as eval_results

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "config" / "models.yaml"
BASELINE = ROOT / "evals" / "baseline.json"
ROUNDS = ROOT / "evals" / "rounds"
COAUTHORING = ROOT / "docs" / "ai-process" / "coauthoring.md"

# The documented measurement behind each voice setting: (path in models.yaml, what it does, the
# measurement or why there is none, its label, where it is written down). Labels follow CLAUDE.md:
# MEASURED, SOURCE or ASSUMED. A test checks every path exists, so a renamed knob fails make check.
KNOBS = (
    ("voice.endpointing.normal.min_delay", "Shortest wait before answering",
     "Turn end decided in about 354 ms on the first real call", "MEASURED",
     "docs/latency.md, First real call"),
    ("voice.endpointing.normal.max_delay", "Longest wait before answering",
     "Starting point from the earlier practice agent, not measured here", "ASSUMED",
     "docs/tuning.md"),
    ("voice.endpointing.digits.min_delay", "Shortest wait after asking for digits",
     "Starting point; the earlier practice agent settled on a 1.5 s cap with 0.35 s of silence",
     "ASSUMED", "docs/latency.md, Where the time is saved"),
    ("voice.endpointing.digits.max_delay", "Longest wait after asking for digits",
     "Starting point, not measured here", "ASSUMED", "docs/tuning.md"),
    ("voice.vad.min_silence_s", "Silence that ends the caller's turn",
     "0.35 to 0.6 s to decide the caller finished, on the earlier practice agent", "MEASURED",
     "docs/latency.md, Plain turn"),
    ("voice.vad.activation", "Voice activity threshold", "Not measured", "ASSUMED",
     "config/models.yaml"),
    ("voice.stt.endpointing_ms", "Deepgram's own end of speech",
     "Final transcript about 250 ms after speech ends (88 to 434) on the first real call",
     "MEASURED", "docs/latency.md, First real call"),
    ("voice.stt.model", "Speech to text model",
     "Final transcript about 250 ms after speech ends on the first real call", "MEASURED",
     "docs/latency.md, First real call"),
    ("voice.tts.model", "Voice model",
     "First audio byte median 216 ms on the first real call", "MEASURED",
     "docs/latency.md, First real call"),
    ("voice.interruption.min_words", "Words before the caller can interrupt",
     "Starting point; tested by live calls 4 and 5", "ASSUMED", "docs/tuning.md"),
    ("voice.interruption.min_duration", "Speech before the caller can interrupt",
     "Starting point; tested by live calls 4 and 5", "ASSUMED", "docs/tuning.md"),
    ("voice.interruption.false_interruption_timeout", "Resume after a false interruption",
     "Starting point; tested by live calls 4 and 5", "ASSUMED", "docs/tuning.md"),
    ("voice.bridge.after_s", "Bridge line after a slow tool",
     "Never fired on the first real call: the slowest tool took 68 ms", "MEASURED",
     "docs/latency.md, First real call"),
    ("voice.bridge.min_turns_between", "Caller turns between bridge lines",
     "Not measured", "ASSUMED", "docs/latency.md"),
)
# Settings named in docs/tuning.md that config/models.yaml doesn't set yet.
UNSET = (
    ("Speaking rate", "voice.tts speed", "1.0, the Cartesia default", "docs/tuning.md"),
    ("Sampling temperature", "the live profile", "1.0, the provider default", "docs/tuning.md"),
    ("Reply length cap", "the live profile's output-token limit", "none", "docs/tuning.md"),
)
# Measurements per model profile, from the latency page and the round readouts.
PROFILE_NOTES = {
    "fast": (("First token median 565 ms on plain turns, thinking off (n=20); 29 of 32 standard "
             "scenarios passed, twice"), "MEASURED",
             "docs/latency.md; evals/rounds/model-matrix-01.md"),
    "fast-prev": ("First token median 472 ms on plain turns (n=16), the control", "MEASURED",
                  "docs/latency.md"),
    "quality": ("First word 1.6 s on plain turns; 26 and 27 of 31 standard scenarios passed",
                "MEASURED", "evals/rounds/model-matrix-01.md"),
}
SIDE_NOTES = {
    "haiku": ("Recall 0.97, precision 1.00 alone; p95 1395 ms", "MEASURED",
              "evals/rounds/side-model-01.md"),
    "openai-decisions": (("Recall 1.00, precision 0.97 alone; p95 511 ms; back before the gate on "
                         "47 of 47 reads"), "MEASURED", "evals/rounds/side-model-01.md"),
    "jev": ("Recall 1.00, precision 0.97 alone; p95 120 ms", "MEASURED",
            "evals/rounds/side-model-01.md"),
}
STACK_NOTE = ("Plain-turn reply median 1.89 s on the audio rig (30 turns), slowest 10% 2.49 s",
              "MEASURED", "evals/rounds/bakeoff-audio-01.md")


def _models() -> dict:
    return yaml.safe_load(MODELS.read_text())


def lookup(config: dict, path: str):
    node = config
    for part in path.split("."):
        node = node[part]
    return node


def knobs(config: dict | None = None) -> list[dict]:
    config = config or _models()
    return [
        {"path": path, "label": label, "value": lookup(config, path), "measure": measure,
         "basis": basis, "source": source}
        for path, label, measure, basis, source in KNOBS
    ]


def profiles(active: str, config: dict | None = None) -> dict:
    """The live profiles and the side models, each with its measurement where one exists."""
    config = config or _models()

    def rows(section: dict, notes: dict) -> list[dict]:
        out = []
        for name, p in section.items():
            measure, basis, source = notes.get(name, ("Not measured", "", ""))
            out.append({"name": name, "provider": p["provider"], "model": p["model"],
                        "thinking": p.get("thinking"), "price": p.get("price") or {},
                        "measure": measure, "basis": basis, "source": source})
        return out

    return {
        "default": config["default"],
        "active": active or config["default"],
        "live": rows(config["profiles"], PROFILE_NOTES),
        "side": rows(config["side"], SIDE_NOTES),
        "offline": rows({**config["offline"], **config["post_call"], **config["owner_edits"]}, {}),
    }


def switches(settings, env: dict) -> list[dict]:
    """The server's own switches. Secrets show only whether they are set."""

    def is_set(value) -> str:
        return "set" if value else "not set"

    return [
        {"name": "Live model profile", "where": "MODEL_PROFILE (voice worker)",
         "value": env.get("MODEL_PROFILE") or "default"},
        {"name": "Side model", "where": "SIDE_MODEL",
         "value": settings.side_model or "off, the phrase rules decide alone"},
        {"name": "Eval gate on publish", "where": "RULES_GATE", "value": "on" if settings.rules_gate else "off"},
        {"name": "Technician summaries", "where": "SUMMARIES", "value": "on" if settings.summaries else "off"},
        {"name": "Weather for priority", "where": "WEATHER", "value": "on" if settings.weather else "off"},
        {"name": "Slot cache", "where": "SLOT_CACHE_SECONDS", "value": f"{settings.slot_cache_seconds:g} s"},
        {"name": "Background jobs", "where": "JOBS_INTERVAL_SECONDS",
         "value": f"every {settings.jobs_interval_seconds:g} s" if settings.jobs_interval_seconds else "off"},
        {"name": "Environment", "where": "APP_ENV", "value": settings.environment},
        {"name": "Recordings", "where": "RECORDING_S3_BUCKET", "value": is_set(settings.recording_bucket)},
        {"name": "Alert email", "where": "SMTP_HOST", "value": is_set(settings.smtp_host)},
        {"name": "Reports to GitHub", "where": "REPORTS_GITHUB_TOKEN", "value": is_set(settings.reports_github_token)},
        {"name": "Debug token", "where": "DEBUG_TOKEN", "value": is_set(settings.debug_token)},
        {"name": "Fixed clock", "where": "FIXED_NOW", "value": settings.fixed_now or "off"},
    ]


# ---- evals ---------------------------------------------------------------------------------

_cache: dict[Path, tuple[float, object]] = {}


def runs(directory: Path = eval_results.RESULTS_DIR) -> list:
    """Every results file that validates, newest first. Parsed once per file change."""
    found = []
    for path in directory.glob("*.json"):
        mtime = path.stat().st_mtime
        cached = _cache.get(path)
        if cached is None or cached[0] != mtime:
            try:
                cached = (mtime, eval_results.load(path))
            except eval_results.ResultsError:
                cached = (mtime, None)
            _cache[path] = cached
        if cached[1] is not None:
            found.append(cached[1])
    return sorted(found, key=lambda r: r.created, reverse=True)


def run_summary(run) -> dict:
    judged = [s for s in run.scenarios if s.outcome != "error"]
    passed = sum(s.outcome == "pass" for s in judged)
    delays = sorted(s.reply_delay_p50_ms for s in judged if s.reply_delay_p50_ms)
    return {
        "run": run,
        "id": run.run_id,
        "created": run.created,
        "suite": run.suite,
        "stack": run.stack,
        "profile": run.profile,
        "transport": run.transport,
        "rate": run.pass_rate(),
        "passed": passed,
        "judged": len(judged),
        "errors": len(run.scenarios) - len(judged),
        "must_pass": sum(bool(s.must_pass_failures) for s in run.scenarios),
        "cost": sum(s.cost_usd for s in run.scenarios),
        "delay": delays[len(delays) // 2] if delays else None,
        "overlay": run.overlay is not None,
    }


def failures(run) -> list:
    return [s for s in run.scenarios if s.outcome != "pass"]


def check_rows(result) -> list[tuple[str, bool, str]]:
    """Each code check that ran, whether it passed, and the failure text when it didn't."""
    failed = {f.split(":", 1)[0]: f.split(":", 1)[1].strip() if ":" in f else f
              for f in result.code_failures}
    rows = [(name, name not in failed, failed.get(name, "")) for name in result.checks]
    rows += [(name, False, why) for name, why in failed.items() if name not in result.checks]
    return rows


def _round_files() -> list[Path]:
    """The eval round write-ups, without the folder's index (evals/rounds/README.md)."""
    return [p for p in sorted(ROUNDS.glob("*.md")) if p.name != "README.md"]


def rounds() -> list[dict]:
    out = [{"slug": p.stem, "title": _title(p), "path": f"evals/rounds/{p.name}"}
           for p in _round_files()]
    if COAUTHORING.exists():
        out.append({"slug": "coauthoring", "title": _title(COAUTHORING),
                    "path": "docs/ai-process/coauthoring.md"})
    return out


def round_path(slug: str) -> Path | None:
    if slug == "coauthoring":
        return COAUTHORING if COAUTHORING.exists() else None
    path = ROUNDS / f"{slug}.md"
    # Only files in the folder, by stem, so a slug can't reach elsewhere.
    return path if path in set(_round_files()) else None


def _title(path: Path) -> str:
    first = path.read_text().split("\n", 1)[0]
    return first.lstrip("# ").strip() or path.stem


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _INLINE_CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _LINK.sub(r"\1", out)


def markdown(source: str) -> str:
    """A small Markdown reader for the rounds: headings, paragraphs, lists, tables and code
    blocks, with inline code and bold. Everything is escaped first; links show as their text."""
    out: list[str] = []
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            body = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            out.append(f'<pre class="tech-block">{html.escape(chr(10).join(body))}</pre>')
        elif line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")) + 1, 4)
            out.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            i -= 1
            head = "".join(f"<th>{_inline(c)}</th>" for c in rows[0]) if rows else ""
            body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                           for r in rows[1:])
            out.append(f'<div class="table-wrap"><table class="table"><thead><tr>{head}</tr>'
                       f"</thead><tbody>{body}</tbody></table></div>")
        elif re.match(r"^\s*(?:[-*]|\d+\.)\s", line):
            items = []
            while i < len(lines) and re.match(r"^\s*(?:[-*]|\d+\.)\s", lines[i]):
                items.append(re.sub(r"^\s*(?:[-*]|\d+\.)\s", "", lines[i]))
                i += 1
            i -= 1
            out.append("<ul>" + "".join(f"<li>{_inline(t)}</li>" for t in items) + "</ul>")
        elif line.strip():
            para = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(
                    r"^(#|\||```|\s*(?:[-*]|\d+\.)\s)", lines[i + 1]):
                i += 1
                para.append(lines[i])
            out.append(f"<p>{_inline(' '.join(para))}</p>")
        i += 1
    return "\n".join(out)


# ---- a real call as a draft eval scenario --------------------------------------------------

FLOW_FROM = (
    ("safety", lambda t: bool(t["call"]["safety_kind"])),
    ("commercial", lambda t: bool(t["call"]["commercial"])),
    ("after-hours", lambda t: bool(t["call"]["after_hours"])),
    ("booking", lambda t: any(b["kind"] == "book" for b in t["bookings"])),
)


def scenario_draft(trace: dict, redact, key: str) -> str:
    """A draft scenario file (evals/scenarios/README.md) from a real call: the caller's first line
    as the opening, the rest as the script, and code checks for what the call did. Personal details
    are redacted and the caller is a made-up number, so the draft needs a person's pass before it
    goes in the suite. `key` is the call's bench key (redact.call_key): the id and the file name
    are built from it, never from the call id, which can carry the caller's number."""
    call = trace["call"]
    said = [e for e in trace["events"] if e["type"] == "turn" and e["speaker"] == "caller"]
    flows = [name for name, test in FLOW_FROM if test(trace)] or ["booking"]
    must: list[dict] = []
    booked = [b for b in trace["bookings"] if b["kind"] == "book" and b["status"] != "failed"]
    if call["safety_kind"]:
        must.append({"safety_lines": "said_first"})
    elif booked:
        must.append({"safety_lines": "not_said"})
    if booked:
        b = booked[0]
        check = {"count": len(booked)}
        if b["priority"]:
            check["priority"] = b["priority"]
        must.append({"booking": check})
        must.append({"read_back": "before_booking"})
    if call["outcome"]:
        must.append({"outcome": call["outcome"]})
    if not must:
        must.append({"outcome": "no_booking"})
    slug = f"replay-{key}"
    draft = {
        "id": slug,
        "flow": flows,
        "difficulty": "standard",
        "journeys": ["spine"],
        "tags": ["replay"],
        "caller_id": "(332) 555-0199",
        "persona": "A caller replayed from a real call. Write who they are and how they talk.",
        "hidden_facts": {"problem": "Fill in from the call."},
        "opening": redact.text(said[0]["text"]) if said else "Hello?",
        "script": [f"Say: {redact.text(e['text'])}" for e in said[1:]],
        "max_turns": max(len(said) + 4, 8),
        "must": must,
        "should": [],
    }
    head = (f"# Draft from call {redact.text(call['id'])}, {call['started_at'][:16]} UTC. "
            "Review the persona, facts and checks\n# before it goes in evals/scenarios/.\n")
    return head + yaml.safe_dump(draft, sort_keys=False, allow_unicode=True, width=100)
