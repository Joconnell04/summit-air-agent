# Build brief: demo walkthrough videos

Branch: `joco/demo-videos-409691`. Issue #168. Depends on nothing in flight: the architecture is settled (DECISIONS.md 112) and these videos show the server, the mock and the owner view, which don't move with agent tuning.

## Goal

Five short silent videos, 1 to 2 minutes each, that teach Jackson how to walk Paul through the live demo. Paul plays the owner of a 40-tech residential HVAC shop seeing the agent for the first time (docs/assignment.md, "Your session"). Each video answers one to three questions that owner is likely to ask, by showing the real tool that proves the answer: an owner view page, a mock ServiceTitan record, a call's timeline, the server code that enforces the rule, and the test that pins it. Captions say what to point at and what to say.

Read first: CLAUDE.md, docs/assignment.md, DECISIONS.md 112, docs/architecture.md, docs/client-notes.md, docs/design-system.md, `scripts/seed_owner_day.py`.

## The five videos

| # | Story | The owner's questions |
|---|---|---|
| 1 | An urgent call, start to finish | "Show me what happened on that 7:42 call and why it's Urgent." "What if they mention grandpa only after hearing the time?" |
| 2 | Gas and carbon monoxide | "What happens with a gas smell?" "What if the fire department already cleared it and they still want a tech?" "Can the AI skip the safety lines?" |
| 3 | The schedule and double-booking | "Prove it didn't double-book that slot." "Is it really in ServiceTitan?" "Can it get the newborn family in sooner?" |
| 4 | Callbacks and leads | "Where do I see the callbacks it promised?" "How do I know the office actually called back?" "What about the calls it couldn't book?" |
| 5 | Your rules, your business | "Can I change my hours or service area?" "Can I change what it says?" "How do I know it won't quote prices?" |

## How they're made

- **A local copy only.** `make owner-demo` seeds a scripted day of 23 calls through the real tool server and mock ServiceTitan (no model, no phone). The mock is served on its own port over the same world so the Schedule page and job records read from it. Nothing touches the live server, the live number or `scripts/reset_live_world`.
- **Real captures.** Headless Playwright screenshots of the owner view and bench at 1600 px wide; real `pytest` and `make trace` output and real mock ServiceTitan API answers, captured to text and drawn as terminal panels; code shown as excerpts of the files at this commit, with file and line.
- **Built with the video-maker kit.** One HTML page per video draws each frame from time alone (pans over the screenshot, a highlight box on what to point at, a caption bar with the line to say), rendered headless to a 1920x1080 MP4 at 30 fps. Silent: captions, no voiceover, no music. No audio is played and no media window opened during checks; checks read contact sheets and stills.
- **No secrets.** Demo accounts and 555 numbers only. No Retell key, token or real phone number other than the demo's own appears in a frame.

## Deliverables

- `docs/demo/videos/NN-<slug>.mp4`, committed only if each is under about 20 MB.
- `docs/demo/NN-<slug>.md`, one page per video: per scenario, the owner's likely ask, what to click or run, what it proves, and a short talk track.
- `docs/demo/README.md` indexing the five and saying how to rebuild them.
- `docs/demo/source/`: the capture script and the video pages, so the videos can be rebuilt after the owner view changes.

## Out of scope

Agent tuning, prompt changes, new owner view features, and Retell's dashboard (it needs the live account and shows keys; the scripts say where to find it instead). Anything wrong found in passing goes to docs/later.md, not fixed here.

## Done

Five MP4s, five scripts, the README, `make check` passing, and a pull request that says `Closes #168`.
