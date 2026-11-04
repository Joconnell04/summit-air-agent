"""A local page for listening to simulated calls: `make listen`, then http://127.0.0.1:8130.

It shows the newest batch in data/sim (evals/audio_rig/recording.py), or the one named by
`?batch=`. Each call has its short code, its scenario, a player and the transcript, each line
numbered and timed; clicking a line plays from there, and space pauses or resumes, so feedback can
point at "S3, line 7" or "S3 at 0:42". It shows outputs only. The grader's verdict is left off,
as it is in the eval rounds, so it doesn't color what the listener hears.

For the person building the agent, on their own laptop; not part of the owner view.
"""
import argparse
import html
import json
import re
from pathlib import Path

from evals.audio_rig.recording import LISTEN_URL, MANIFEST, SIM_DIR

FILE_NAME = re.compile(r"^S\d+\.(flac|wav)$")
BATCH_NAME = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9_-]+$")


def batches(root: Path = SIM_DIR) -> list[str]:
    """Batch folders that have a manifest, newest first (their names start with the time)."""
    if not root.is_dir():
        return []
    names = [p.name for p in root.iterdir()
             if BATCH_NAME.match(p.name) and (p / MANIFEST).is_file()]
    return sorted(names, reverse=True)


def listing(root: Path, name: str) -> dict:
    """One batch: its manifest, with each call's transcript read in."""
    folder = root / name
    manifest = json.loads((folder / MANIFEST).read_text())
    calls = []
    for entry in manifest.get("calls", []):
        path = folder / entry["transcript"]
        transcript = json.loads(path.read_text()) if path.is_file() else {"lines": []}
        calls.append({**entry, "persona": transcript.get("persona", ""),
                      "lines": transcript.get("lines", [])})
    return {**manifest, "calls": calls}


def clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def render(batch: dict, others: list[str]) -> str:
    e = html.escape
    picker = "".join(
        f'<option value="{e(n)}"{" selected" if n == batch["batch"] else ""}>{e(n)}</option>'
        for n in others
    )
    sections = []
    for call in batch["calls"]:
        rows = []
        for line in call["lines"]:
            pause = (f'<span class="pause">{line["reply_delay_ms"] / 1000:.1f} s</span>'
                     if "reply_delay_ms" in line else "")
            rows.append(
                f'<li data-t="{line["t"]}" class="{e(line["speaker"])}">'
                f'<span class="n">{line["n"]}</span>'
                f'<span class="t">{clock(line["t"])}</span>'
                f'<span class="who">{e(line["speaker"].title())}</span>'
                f'<span class="text">{e(line["text"])}{pause}</span></li>'
            )
        src = f'/audio/{e(batch["batch"])}/{e(call["audio"])}'
        sections.append(
            f'<section id="{e(call["code"])}"><header><div class="code">{e(call["code"])}</div>'
            f'<div><div class="scenario">{e(call["scenario_id"])}'
            f'{" (sample " + str(call["sample"]) + ")" if call.get("sample", 1) > 1 else ""}</div>'
            f'<div class="persona">{e(call.get("persona", ""))}</div></div>'
            f'<div class="length">{clock(call.get("duration_s", 0))}</div></header>'
            f'<audio controls preload="metadata" src="{src}"></audio>'
            f'<ol>{"".join(rows)}</ol></section>'
        )
    body = "".join(sections) or "<p>No calls in this batch yet.</p>"
    return PAGE.format(title=e(batch["batch"]), picker=picker, body=body,
                       count=len(batch["calls"]))


def empty_page() -> str:
    return PAGE.format(title="No batches", picker="", count=0,
                       body="<p>No simulated calls yet. Run <code>make sim CALLS=...</code>.</p>")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Simulated calls: {title}</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; background: #141518; color: #e6e6e6;
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
.top {{ position: sticky; top: 0; z-index: 1; display: flex; gap: 12px; align-items: center;
  padding: 10px 24px; background: #1b1c20; border-bottom: 1px solid #2a2b30; }}
.top h1 {{ font-size: 15px; font-weight: 600; margin: 0; }}
.top .hint {{ color: #8a8c93; font-size: 13px; margin-left: auto; }}
select {{ background: #24252a; color: inherit; border: 1px solid #34353b; border-radius: 6px;
  height: 28px; padding: 0 8px; font: inherit; font-size: 13px; }}
main {{ max-width: 920px; margin: 0 auto; padding: 16px 24px 80px; }}
section {{ border: 1px solid #2a2b30; border-radius: 8px; margin: 16px 0; padding: 16px 20px;
  background: #18191c; }}
section.active {{ border-color: #4b6bd6; }}
header {{ display: flex; gap: 16px; align-items: center; }}
.code {{ font-size: 40px; font-weight: 700; min-width: 72px; letter-spacing: -0.5px; }}
.scenario {{ font-weight: 600; }}
.persona {{ color: #9a9ca3; font-size: 13px; }}
.length {{ margin-left: auto; color: #8a8c93; font-variant-numeric: tabular-nums; }}
audio {{ width: 100%; margin: 12px 0 4px; }}
ol {{ list-style: none; margin: 0; padding: 0; }}
li {{ display: grid; grid-template-columns: 32px 44px 56px 1fr; gap: 8px; padding: 5px 6px;
  border-radius: 6px; cursor: pointer; }}
li:hover {{ background: #202126; }}
li.now {{ background: #232a40; }}
.n, .t {{ color: #8a8c93; font-variant-numeric: tabular-nums; text-align: right; }}
.who {{ color: #8a8c93; }}
li.agent .who {{ color: #8fb0ff; }}
.pause {{ color: #6f7178; font-size: 12px; margin-left: 8px; }}
code {{ background: #24252a; padding: 1px 5px; border-radius: 4px; }}
</style></head>
<body>
<div class="top"><h1>{count} simulated calls</h1>
<select onchange="location.search='?batch='+this.value">{picker}</select>
<span class="hint">Click a line to play from it. Space pauses.</span></div>
<main>{body}</main>
<script>
const players = [...document.querySelectorAll("audio")];
let active = players[0] || null;
function activate(player) {{
  active = player;
  document.querySelectorAll("section").forEach(s => s.classList.toggle("active",
    s.contains(player)));
}}
players.forEach(p => {{
  p.addEventListener("play", () => {{
    players.forEach(o => {{ if (o !== p) o.pause(); }});
    activate(p);
  }});
  p.addEventListener("timeupdate", () => {{
    const rows = [...p.closest("section").querySelectorAll("li")];
    let current = null;
    rows.forEach(r => {{ if (parseFloat(r.dataset.t) <= p.currentTime + 0.05) current = r; }});
    rows.forEach(r => r.classList.toggle("now", r === current));
  }});
}});
document.querySelectorAll("li").forEach(row => row.addEventListener("click", () => {{
  const player = row.closest("section").querySelector("audio");
  player.currentTime = parseFloat(row.dataset.t);
  player.play();
}}));
document.addEventListener("keydown", event => {{
  if (event.code !== "Space" || !active || event.target.tagName === "SELECT") return;
  event.preventDefault();
  if (active.paused) active.play(); else active.pause();
}}, true);
</script>
</body></html>
"""


def create_app(root: Path = SIM_DIR):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", response_class=HTMLResponse)
    def page(batch: str | None = None) -> str:
        names = batches(root)
        if not names:
            return empty_page()
        if batch is None:
            batch = names[0]
        if batch not in names:
            raise HTTPException(404, "no such batch")
        return render(listing(root, batch), names)

    @app.get("/audio/{batch}/{name}")
    def audio(batch: str, name: str):
        # Only a call's own file in a known batch, so a crafted path can't read anything else.
        if batch not in batches(root) or not FILE_NAME.match(name):
            raise HTTPException(404, "no such file")
        path = root / batch / name
        if not path.is_file():
            raise HTTPException(404, "no such file")
        kind = "audio/flac" if path.suffix == ".flac" else "audio/wav"
        return FileResponse(path, media_type=kind)

    return app


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the simulated-call listening page.")
    parser.add_argument("--dir", default=str(SIM_DIR))
    args = parser.parse_args(argv)
    port = int(LISTEN_URL.rsplit(":", 1)[1])
    print(f"listening page: {LISTEN_URL}", flush=True)
    uvicorn.run(create_app(Path(args.dir)), host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
