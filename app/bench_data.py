"""What the debug bench (app/bench.py) reads besides the call record: the live call's voice
settings and the models in use with the measurement behind each, the server's own switches, the
eval runs in evals/results and the rounds in evals/rounds, and a draft eval scenario made from a
real call. All read-only.
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

# The live call's voice settings: Retell's dials on the socket agent, which copies them from
# Jackson's agent (retell/socket/push.py; snapshot in retell/summit-air-jackson/agent.json), the
# socket push's own override, and the socket's hold phrase (config/models.yaml). Each row: label,
# where the value is read (file and field), what it does, and why it has this value. The why's
# source is in the row's tooltip: docs/retell-knobs.md, the round named, or the decision. A test
# checks every field is found, so a renamed setting fails make check.
AGENT_SNAPSHOT = ROOT / "retell" / "summit-air-jackson" / "agent.json"
SOCKET_PUSH = ROOT / "retell" / "socket" / "push.py"
VOICE_KNOBS = (
    ("Transcription", ("agent", "stt_mode"), "How Retell turns the caller's speech into text",
     "Fast mode missed a quiet one-word \"sure\"; accurate heard 12 of 12 short replies",
     "MEASURED, docs/retell-knobs.md (replay of live call 3)"),
    ("Noise filter", ("agent", "denoising_mode"), "Cleans line noise before transcription",
     "With accurate transcription it heard every reply; retell/socket/denoise.py switches it",
     "MEASURED, docs/retell-knobs.md"),
    ("Responsiveness", ("agent", "responsiveness"), "How soon the agent answers once the caller stops",
     "Answered short replies in 1.7 s and still waited through a pause mid-sentence",
     "MEASURED, docs/retell-knobs.md"),
    ("Interruptions", ("agent", "interruption_sensitivity"), "How easily the caller cuts the agent off",
     "Retell's default; their advice for noisy lines is 0.8, not tried on our calls",
     "SOURCE, docs.retellai.com/build/handle-background-noise"),
    ("Backchannel", ("agent", "backchannel_frequency"), "\"Mm-hmm\" and \"okay\" while the caller talks",
     "Never talked over a replayed caller, and the wait feels shorter",
     "MEASURED, docs/retell-knobs.md; DECISIONS.md 127"),
    ("Check-in", ("agent", "reminder_trigger_ms"), "Silence before the agent asks if the caller is there",
     "At 6 s it cut in on a caller who was thinking", "MEASURED, docs/retell-knobs.md (call 2)"),
    ("Hang-up on silence", ("push", "SILENCE_HANG_UP_MS"), "Ends a call the caller walked away from",
     "Matches the dead-air goodbye at 8 minutes", "DECISIONS.md 081"),
    ("Hold phrase", ("models", "voice.hold_phrase_after_s"), "How long a lookup runs before \"One sec.\"",
     "At 0.7 s it played on 1 caller turn in 5", "MEASURED, evals/rounds/socket-buildout-01.md"),
    ("Voice", ("agent", "voice_id"), "Brian's voice, at Retell's normal pace",
     "Retell sets pace per agent, so it can't slow down for one caller", "docs/tuning.md"),
)
# The models in use, by job, with the one fact that matters for the demo. Profiles are read from
# config/models.yaml; the side model shows only when SIDE_MODEL turns one on.
MODEL_JOBS = (
    ("Replies", ("profiles", None), "First word 0.57 s on plain turns; 91% of the standard suite",
     "MEASURED, evals/rounds/model-matrix-01.md"),
    ("Tried for replies", ("profiles", "quality"), "First word 1.6 s; 84 to 87%; narrated its reasoning",
     "MEASURED, evals/rounds/model-matrix-01.md, sonnet-buildout-01.md"),
    ("Technician summary", ("post_call", "summary"), "Written after the call ends", ""),
    ("Failure grader", ("offline", "grader"), "Labels each ended call", "evals/rounds/failure-grader-01.md"),
    ("Owner rule check", ("owner_edits", "guidance_check"), "Checks an owner's draft rule on save", ""),
    ("Simulated caller", ("offline", "caller"), "Plays the caller in evals", ""),
)


def baseline() -> dict:
    """evals/baseline.json: the eval floor and the reference run."""
    import json

    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {}


def _snapshot() -> dict:
    import json

    return json.loads(AGENT_SNAPSHOT.read_text())


def _push_constant(name: str):
    found = re.search(rf"^{name} = ([0-9_]+)", SOCKET_PUSH.read_text(), re.MULTILINE)
    return int(found.group(1).replace("_", "")) if found else None


def live_number() -> str:
    """The number the socket agent answers (retell/socket/push.py NUMBER), as written."""
    found = re.search(r'^NUMBER = "\+1(\d{3})(\d{3})(\d{4})"', SOCKET_PUSH.read_text(), re.MULTILINE)
    return f"({found.group(1)}) {found.group(2)}-{found.group(3)}" if found else ""


def _shown_knob(field: str, value, agent: dict) -> str:
    if value is None:
        return "-"
    if field == "stt_mode":
        return str(value).capitalize()
    if field == "denoising_mode":
        return str(value).replace("-", " ").capitalize()
    if field == "backchannel_frequency":
        return f"On, {value:g}" if agent.get("enable_backchannel") else "Off"
    if field in ("responsiveness", "interruption_sensitivity"):
        return f"{value:.1f}"
    if field == "reminder_trigger_ms":
        times = agent.get("reminder_max_count", 1)
        return f"{value / 1000:g} s, {'once' if times == 1 else f'up to {times}'}"
    if field == "SILENCE_HANG_UP_MS":
        return f"{value / 60000:g} min"
    if field == "voice.hold_phrase_after_s":
        return f"{value:g} s"
    if field == "voice_id":
        return f"{str(value).split('-', 1)[-1]}, {agent.get('voice_speed', 1):g}x"
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def voice_knobs(config: dict | None = None) -> list[dict]:
    config = config or _models()
    agent = _snapshot()
    out = []
    for label, (where, field), does, why, source in VOICE_KNOBS:
        if where == "agent":
            value, place = agent.get(field), f"Retell agent, {field}"
        elif where == "push":
            value, place = _push_constant(field), f"retell/socket/push.py, {field}"
        else:
            value, place = lookup(config, field), f"config/models.yaml, {field}"
        out.append({"label": label, "field": field, "where": place, "value": value,
                    "shown": _shown_knob(field, value, agent), "does": does, "why": why,
                    "source": source})
    return out


def models_in_use(side_model: str, active: str, config: dict | None = None) -> list[dict]:
    """The models the system runs, by job. The replies row is the live profile."""
    config = config or _models()
    rows = []
    for job, (section, name), note, source in MODEL_JOBS:
        name = name or active or config["default"]
        p = config[section].get(name)
        if p is None or (job == "Tried for replies" and name == (active or config["default"])):
            continue
        rows.append({"job": job, "profile": name, "model": p["model"], "provider": p["provider"],
                     "price": p.get("price") or {}, "note": note, "source": source,
                     "live": job == "Replies"})
    side = config["side"].get(side_model) if side_model else None
    rows.insert(1, {"job": "Side model", "profile": side_model or "off",
                    "model": side["model"] if side else "", "provider": side["provider"] if side else "",
                    "price": (side or {}).get("price") or {},
                    "note": "Can only raise a call's priority" if side else "Off: the phrase rules decide alone",
                    "source": "DECISIONS.md 006", "live": bool(side)})
    return rows


def _models() -> dict:
    return yaml.safe_load(MODELS.read_text())


def lookup(config: dict, path: str):
    node = config
    for part in path.split("."):
        node = node[part]
    return node


def switches(settings) -> list[dict]:
    """The server switches worth naming on the walkthrough. Secrets show only whether they are set."""

    def on(value) -> str:
        return "On" if value else "Off"

    return [
        {"name": "Eval gate on publish", "where": "RULES_GATE", "value": on(settings.rules_gate),
         "does": "An owner rule goes live only after the must-pass evals pass"},
        {"name": "Technician summaries", "where": "SUMMARIES", "value": on(settings.summaries),
         "does": "Writes the job summary after the call"},
        {"name": "Weather", "where": "WEATHER", "value": on(settings.weather),
         "does": "Outside temperature for no-heat priority"},
        {"name": "Slot cache", "where": "SLOT_CACHE_SECONDS", "value": f"{settings.slot_cache_seconds:g} s",
         "does": "How long find_slots reuses ServiceTitan's capacity"},
        {"name": "Background jobs", "where": "JOBS_INTERVAL_SECONDS",
         "value": f"Every {settings.jobs_interval_seconds:g} s" if settings.jobs_interval_seconds else "Off",
         "does": "Retries queued bookings, places callbacks, writes summaries"},
        {"name": "Recordings", "where": "RECORDING_S3_BUCKET",
         "value": "Set" if settings.recording_bucket else "Not set", "does": "Where call audio is kept"},
        {"name": "Alert email", "where": "AGENTMAIL_API_KEY or SMTP_HOST",
         "value": "Set" if settings.agentmail_api_key or settings.smtp_host else "Held, not sent",
         "does": "Emails the on-call person after hours"},
        {"name": "Fixed clock", "where": "FIXED_NOW", "value": settings.fixed_now or "Off",
         "does": "Pins the server's time for a demo world"},
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


# What each scenario tests, from its flow labels (evals/README.md), as the Evals filter names it.
TESTED = {
    "safety": "Safety",
    "priority": "Priority",
    "booking": "Booking",
    "repeat-caller": "Repeat caller",
    "membership": "Membership",
    "renter": "Renter",
    "commercial": "Commercial",
    "after-hours": "After hours",
    "injection": "Prompt attacks",
    "off-topic": "Off topic",
    "spam": "Off topic",
}
# The stacks as the Evals page names them; any other is shown as recorded.
STACK_NAMES = {
    "livekit": "LiveKit", "livekit-rig": "LiveKit, audio rig", "retell_llm": "Retell socket",
    "retell_flow": "Retell flow", "retell-flow-rig": "Retell flow, audio rig",
    "retell-rig": "Retell, audio rig",
}
# A run this size or bigger is a suite run; smaller ones are reruns of a scenario or two.
SUITE_MIN = 10


def tested_key(label: str) -> str:
    return label.lower().replace(" ", "-")


def tested_labels(scenario) -> set[str]:
    flows = scenario.flow if isinstance(scenario.flow, list) else [scenario.flow]
    return {TESTED[f] for f in flows if f in TESTED}


def run_summary(run, tested: str = "") -> dict:
    """A run's numbers, over its scenarios that test `tested` (a TESTED label key) when given."""
    scenarios = [s for s in run.scenarios
                 if not tested or tested in {tested_key(x) for x in tested_labels(s)}]
    judged = [s for s in scenarios if s.outcome != "error"]
    passed = sum(s.outcome == "pass" for s in judged)
    delays = sorted(s.reply_delay_p50_ms for s in judged if s.reply_delay_p50_ms)
    counts: dict[str, int] = {}
    for s in run.scenarios:
        for label in tested_labels(s):
            counts[label] = counts.get(label, 0) + 1
    return {
        "run": run,
        "id": run.run_id,
        "created": run.created,
        "suite": run.suite,
        "stack": run.stack,
        "stack_name": STACK_NAMES.get(run.stack, run.stack.replace("_", " ")),
        "profile": run.profile,
        "transport": run.transport,
        "rate": (passed / len(judged)) if judged else None,
        "passed": passed,
        "judged": len(judged),
        "errors": len(scenarios) - len(judged),
        "size": len(run.scenarios),
        "must_pass": sum(bool(s.must_pass_failures) for s in scenarios),
        "cost": sum(s.cost_usd for s in scenarios),
        "delay": delays[len(delays) // 2] if delays else None,
        "overlay": run.overlay is not None,
        "tested": [label for label, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]],
    }


def tested_choices(runs: list) -> list[tuple[str, str, int]]:
    """The TESTED labels that some run covers: key, label, and how many runs cover it."""
    seen: dict[str, int] = {}
    for run in runs:
        for label in {x for s in run.scenarios for x in tested_labels(s)}:
            seen[label] = seen.get(label, 0) + 1
    order = list(dict.fromkeys(TESTED.values()))
    return [(tested_key(label), label, seen[label]) for label in order if label in seen]


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
    """The rounds' write-ups; the folder's README is its index, not a round."""
    return [p for p in sorted(ROUNDS.glob("*.md")) if p.name != "README.md"]


_ROUND_ROW = re.compile(r"^\| (.+?) \| (.+?) \|$", re.MULTILINE)
_ROUND_FILE = re.compile(r"\]\(([\w-]+)\.md\)")
_RUN_STAMP = re.compile(r"\b(\d{8}-\d{6})\b")


def _settled() -> dict[str, str]:
    """What each round settled, from the table in evals/rounds/README.md, by file stem."""
    readme = ROUNDS / "README.md"
    if not readme.exists():
        return {}
    out = {}
    for files, settled in _ROUND_ROW.findall(readme.read_text()):
        for stem in _ROUND_FILE.findall(files):
            out[stem] = settled.strip()
    return out


def rounds() -> list[dict]:
    """Every round, newest first (the README's table is oldest first), with what it settled and
    the results files it names."""
    settled = _settled()
    order = {stem: i for i, stem in enumerate(settled)}
    found = []
    for p in _round_files():
        text = p.read_text()
        found.append({"slug": p.stem, "title": _title(p), "path": f"evals/rounds/{p.name}",
                      "settled": settled.get(p.stem, ""),
                      "runs": sorted(set(_RUN_STAMP.findall(text)))})
    found.sort(key=lambda r: order.get(r["slug"], len(order)), reverse=True)
    if COAUTHORING.exists():
        found.append({"slug": "coauthoring", "title": _title(COAUTHORING),
                      "path": "docs/ai-process/coauthoring.md",
                      "settled": "Jackson's reads of real calls, round by round", "runs": []})
    return found


def round_of(found: list[dict]) -> dict[str, dict]:
    """The round that names each results file, by the file's date and time stamp."""
    out: dict[str, dict] = {}
    for r in found:
        for stamp in r["runs"]:
            out.setdefault(stamp, r)
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



# ---- prompt versions -----------------------------------------------------------------------

PROMPT_LOG = ROOT / "retell" / "PROMPT-LOG.md"
SOCKET_SECTION = "\n## Socket build (issue #176)"
REPO_COMMITS = "https://github.com/Joconnell04/summit-air-agent/commit/"
_VERSION_ROW = re.compile(r"^\| (v\d+) \| ([^|]*) \| ([\d,]+) \| (\d+) \| (\w+) \|$", re.MULTILINE)
_VERSION_HEAD = re.compile(r"^### (v\d+) \(([^)]*)\)\s*$", re.MULTILINE)
_OUTCOME = re.compile(
    r'(?:Outcome: |(?<=\. )|(?<=\." )|^)(Kept|Mostly kept|Dropped|Replaced|tests pass|checked|Not measured|'
    r"Not on a call yet|Being measured)[^.]*\.", re.MULTILINE | re.IGNORECASE)
_TIME_OR_COMMIT = re.compile(r"^(\d{2}:\d{2}|[0-9a-f]{7})$")


def _first_sentence(text: str) -> str:
    plain = " ".join(line.strip("- ").strip() for line in text.splitlines() if line.strip())
    found = re.match(r"(.+?[.!?])(\s|$)", plain)
    return (found.group(1) if found else plain)[:240]


def prompt_versions(path: Path = PROMPT_LOG) -> list[dict]:
    """The live socket prompt's versions (retell/socket/prompt.md), newest first, from the socket
    section of retell/PROMPT-LOG.md: the measured table (time, words, "never" count, commit) and
    each version's entry (its title, first sentence, outcome and the whole entry)."""
    if not path.exists():
        return []
    text = path.read_text()
    start = text.find(SOCKET_SECTION)
    if start < 0:
        return []
    end = text.find("\n## ", start + len(SOCKET_SECTION))
    section = text[start:end if end > 0 else None]
    table = {m.group(1): m for m in _VERSION_ROW.finditer(section)}
    heads = list(_VERSION_HEAD.finditer(section))
    out = []
    for i, head in enumerate(heads):
        version, label = head.group(1), head.group(2).strip()
        body_end = heads[i + 1].start() if i + 1 < len(heads) else len(section)
        body = section[head.end():body_end]
        body = body[:body.find("\n### ")] if "\n### " in body else body
        row = table.get(version)
        outcome = _OUTCOME.search(body)
        out.append({
            "version": version,
            "number": int(version[1:]),
            "title": "" if _TIME_OR_COMMIT.match(label) else label[:1].upper() + label[1:],
            "summary": _first_sentence(body),
            "outcome": outcome.group(0).removeprefix("Outcome: ").rstrip(".") if outcome else "",
            "time": row.group(2).strip() if row else "",
            "words": row.group(3) if row else "",
            "never": row.group(4) if row else "",
            "commit": row.group(5) if row else "",
            "commit_url": f"{REPO_COMMITS}{row.group(5)}" if row else "",
            "body": markdown(body.strip()),
        })
    return sorted(out, key=lambda v: v["number"], reverse=True)

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
