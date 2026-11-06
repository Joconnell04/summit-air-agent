"""Turn each video spec in docs/demo/source/specs/ into a page the video-maker renderer can draw.

A spec lists segments (cards, screenshots, terminal captures, code excerpts) and, inside each, the
steps the camera and captions move through. This script times every step from the words on screen,
reads the real lines out of the captures and source files, and writes docs/demo/source/NN-slug.html
with the whole film inlined as window.FILM.

Run: uv run python docs/demo/source/build.py [NN ...]
"""

import json
import struct
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PANEL_W, PANEL_H = 1792, 704
SHOT_W = 1600  # the screenshots' width in page pixels (captured at 2x)
READ_WPS = 3.4  # words a second a viewer reads comfortably while also looking at the screen
SAY_MAX_CHARS = 190  # two caption lines at the caption size
VISIBLE_LINES = 18


def words(*texts: str) -> int:
    return sum(len((t or "").split()) for t in texts)


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        head = f.read(24)
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def step_time(step: dict, moved: bool) -> float:
    if "dur" in step:
        return float(step["dur"])
    base = 1.3 + words(step.get("show"), step.get("say")) / READ_WPS
    return round(base + (0.7 if moved else 0), 2)


def pick_lines(text: str, ranges: list) -> list[str]:
    lines = text.splitlines()
    if not ranges:
        return lines
    out = []
    for a, b in ranges:
        out += lines[a - 1 : b]
    return out


def check_caption(where: str, item: dict) -> None:
    for key in ("show", "say", "big", "sub", "kicker"):
        value = item.get(key) or ""
        if chr(0x2014) in value or chr(0x2013) in value:
            raise SystemExit(f"{where}: a dash in {key}; use a comma, semicolon or full stop")
    if len(item.get("say") or "") > SAY_MAX_CHARS:
        raise SystemExit(f"{where}: say is {len(item['say'])} characters, over {SAY_MAX_CHARS}")


def build(spec_path: Path) -> tuple[Path, float]:
    spec = yaml.safe_load(spec_path.read_text())
    t = 0.0
    segments = []
    for n, seg in enumerate(spec["segments"]):
        seg = dict(seg)
        where = f"{spec_path.name} segment {n + 1}"
        seg["t0"] = t
        kind = seg["kind"]
        if kind in ("intro", "ask", "outro"):
            check_caption(where, seg)
            dur = seg.get("dur") or step_time(seg, False)
            seg["t1"] = t = round(t + dur, 3)
            segments.append(seg)
            continue
        if kind == "image":
            w, h = png_size(HERE / "shots" / seg["src"])
            seg["w"], seg["h"] = SHOT_W, h * SHOT_W // w
            seg["src"] = f"shots/{seg['src']}"
        elif kind == "term":
            text = (HERE / "captures" / seg["capture"]).read_text()
            seg["lines"] = pick_lines(text, seg.get("pick"))
            seg.setdefault("label", "")
        elif kind == "code":
            a, b = seg["lines"]
            source = (ROOT / seg["file"]).read_text().splitlines()
            seg["first"] = a
            seg["lines"] = source[a - 1 : b]
            seg["label"] = f"{seg['file']}, lines {a} to {b}"
        else:
            raise SystemExit(f"{where}: unknown kind {kind}")
        prev_cam = None
        steps = []
        for m, step in enumerate(seg["steps"]):
            step = dict(step)
            check_caption(f"{where} step {m + 1}", step)
            if kind == "image":
                if "zoom" in step:
                    # A camera of that width, centred on the step's box.
                    cw, (bx, by, bw, bh) = step.pop("zoom"), step["box"]
                    cam = [bx + bw / 2 - cw / 2, by + bh / 2 - PANEL_H * cw / PANEL_W / 2, cw]
                else:
                    cam = step.get("cam") or prev_cam or [0, 0, SHOT_W]
                x, y, cw = cam
                x = max(0, x)
                visible = PANEL_H * cw / PANEL_W
                step["cam"] = [min(x, SHOT_W - cw), max(0, min(y, seg["h"] - visible)), cw]
                moved = prev_cam is not None and step["cam"] != prev_cam
                prev_cam = step["cam"]
            else:
                if "scroll" not in step and steps:
                    step["scroll"] = steps[-1].get("scroll", 0)
                moved = bool(steps) and step.get("scroll", 0) != steps[-1].get("scroll", 0)
                if len(seg["lines"]) > VISIBLE_LINES and "scroll" not in step:
                    step["scroll"] = 0
            step["t0"] = t
            t = round(t + step_time(step, moved), 3)
            step["t1"] = t
            steps.append(step)
        seg["steps"] = steps
        seg["t1"] = t
        segments.append(seg)

    film = {
        "num": spec["num"],
        "total": spec["total"],
        "title": spec["title"],
        "chips": spec["chips"],
        "duration": round(t, 3),
        "segments": segments,
    }
    page = (HERE / "player" / "page.html").read_text()
    out = HERE / f"{spec_path.stem}.html"
    out.write_text(page.replace("/*FILM*/", "window.FILM = " + json.dumps(film, indent=1) + ";"))
    return out, t


def main(argv: list[str]) -> None:
    specs = sorted((HERE / "specs").glob("*.yaml"))
    if argv:
        specs = [s for s in specs if any(s.name.startswith(a) for a in argv)]
    for spec in specs:
        out, seconds = build(spec)
        flag = "" if 60 <= seconds <= 120 else "  (outside 1 to 2 minutes)"
        print(f"{out.relative_to(ROOT)}  {seconds:.1f} s{flag}")


if __name__ == "__main__":
    main(sys.argv[1:])
