# Demo walkthroughs

Five short silent videos that walk through the questions the owner of a 40-tech residential HVAC shop is likely to ask in the demo, each answered with the real tool that proves it. Each has a one-page script beside it: the ask, what to click or run, what it proves, and a talk track. The captions say what to point at and what to say.

| # | Video | Script | The owner's questions |
|---|---|---|---|
| 1 | [An urgent call, start to finish](videos/01-urgent-call.mp4) | [01-urgent-call.md](01-urgent-call.md) | What happened on that call? Why is it Urgent? What if they mention grandpa after hearing the wait? |
| 2 | [Gas and carbon monoxide](videos/02-gas-and-co.mp4) | [02-gas-and-co.md](02-gas-and-co.md) | What happens with a gas smell? The fire department cleared it, can they still book? Can the AI skip the safety lines? |
| 3 | [The schedule and double-booking](videos/03-schedule.mp4) | [03-schedule.md](03-schedule.md) | Prove it didn't double-book. Is it really in ServiceTitan? Can it get the newborn family in sooner? |
| 4 | [Callbacks and leads](videos/04-callbacks.mp4) | [04-callbacks.md](04-callbacks.md) | Where are the callbacks it promised? How do I know the office called back? What about the calls it couldn't book? |
| 5 | [Your rules, your business](videos/05-your-rules.mp4) | [05-your-rules.md](05-your-rules.md) | Can I change my hours or area? Can I change what it says? How do I know it won't quote prices? |

A suggested order for the meeting: 1 right after the first live call (it's the requirement the owner named on the kickoff call), 2 when safety comes up or right after, 3 and 4 when the owner asks how it fits the office, and 5 near the end, ahead of next steps.

## What the videos show, and what they don't

- **A seeded day, not the live server.** Every page is from `make owner-demo`: 23 scripted calls run through the real tool server and the mock ServiceTitan on a moved clock, no model and no phone (`scripts/seed_owner_day.py`). The screens, the rules and the checks are the same code as the live server; the calls are scripted, so the words in them are stand-ins for real callers. In the live demo, open the same pages on the live owner view for the calls Paul just made.
- **What isn't built on the Retell line.** Outbound calls (the earlier-opening call in video 3, and calls the system places back) were built for the first voice stack only (voice/retell/README.md). The videos say so where it comes up.
- **Known quirks of the seeded day.** A few names and two out-of-area examples in the seed disagree with today's rules (docs/later.md, C-79). The videos avoid them.

## Rebuilding the videos

From the repo root, after `make setup`:

1. Seed the day: `uv run python -m scripts.seed_owner_day --dir data/owner-demo`.
2. Serve it with the mock ServiceTitan beside it: `sh docs/demo/source/serve.sh` (ports 8120 and 8121, local only).
3. Capture the pages and the terminal panels: `node docs/demo/source/capture.mjs` and `sh docs/demo/source/capture_text.sh`.
4. Build the pages from the specs: `uv run python docs/demo/source/build.py`.
5. Render each with the video-maker kit: `node <kit>/scripts/render.mjs docs/demo/source/01-urgent-call.html --out docs/demo/videos/01-urgent-call.mp4 --crf 20`.

`docs/demo/source/specs/` holds one YAML file per video: its segments, the camera and highlight box on each screenshot, and the Show and Say captions. Boxes are in page pixels of the 1600 px screenshots; `build.py` times each step from the words on screen and reads code and capture lines from the files at the current commit, so an excerpt can't drift from the code it names. The player (`source/player/`) draws each frame from time alone, as the renderer needs.
