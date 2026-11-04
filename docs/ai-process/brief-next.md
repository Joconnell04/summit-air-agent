# Brief: where things stand, and the live calls

Written at the end of the check-ins while Jackson was away (about 23:00 to 01:10). What's live, what's open, what was decided without him, and what needs him.

## Needs Jackson

1. **The 10 live calls: the number is ready now.** (646) 624-0418, script in `evals/live-calls.md`. Everything below is deployed and a scripted test call passed at 01:10. Recommend calling now rather than waiting for the last fix pass (below): real-call evidence is the biggest gap, and the remaining fixes are small. Three things to listen for, seen on the test call:
   - The relative day is wrong just after midnight: a November 4 visit was called "tomorrow" at 1 AM. Fixed in the open pass; daytime calls aren't affected.
   - The first words of the goodbye may be clipped when the call ends ("...for calling Summit Air").
   - "Sure, Pat" ran together; listen for whether names after "Sure" sound natural.
   If a caller asks for a person, the transfer rings your cell.
2. **After the calls, one feedback pass.** Say what felt off; I'll map each complaint to a setting in `docs/tuning.md` (end-of-turn wait, interruption sensitivity, reply length cap, sampling temperature, speaking rate) and try the obvious fix first. One is already queued: a lower turn-detector maximum (now 1.5 s), since on about one turn in seven the detector waited the full 1.5 s on a finished line (MEASURED, PR #98).

Nothing else needs a decision. No balance needs a top-up: no run failed for credit tonight. Tonight's paid runs come to about $27 (from the lanes' reports: round 3 and 4 about $16.60, the side-model check $2.50, end-of-turn runs $2.07, the listener batch $1.60, speaking rate and ZIP $1.25, the rest under $1 each), with up to $8 more for the last fix pass.

## Live on the server (deployed 01:05, health checks pass)

| What | PR | Decision |
|---|---|---|
| Round 3 and 4 wording: read-back and booking confirmations spoken by the server, a speech filter for repeats, tool talk and unprompted advice, the known-details list | [#82](https://github.com/Joconnell04/summit-air-agent/pull/82) | 060 |
| Speaking rate per call, set by the server and remembered | [#83](https://github.com/Joconnell04/summit-air-agent/pull/83) | 053 |
| Admin and staff accounts and a settings panel with the recording-notice switch | [#85](https://github.com/Joconnell04/summit-air-agent/pull/85) | 054 |
| Two calls racing for one slot, tested end to end | [#86](https://github.com/Joconnell04/summit-air-agent/pull/86), [#94](https://github.com/Joconnell04/summit-air-agent/pull/94) | 057 |
| "Nobody's elderly" no longer reads as someone at risk; the side model stays on | [#87](https://github.com/Joconnell04/summit-air-agent/pull/87) | 056 |
| The server acts only on a ZIP the caller said; street names like Church Ave aren't businesses | [#88](https://github.com/Joconnell04/summit-air-agent/pull/88), [#91](https://github.com/Joconnell04/summit-air-agent/pull/91), [#92](https://github.com/Joconnell04/summit-air-agent/pull/92) | 058 |
| Debug bench at `/bench`: calls as a node graph with prompts and rule decisions, settings, evals, live view | [#89](https://github.com/Joconnell04/summit-air-agent/pull/89) | 059 |
| Cold handoff when a caller demands a person | [#90](https://github.com/Joconnell04/summit-air-agent/pull/90) | 061 |
| Rule editing moved to `/bench/rules`, with a logged override | [#93](https://github.com/Joconnell04/summit-air-agent/pull/93), [#96](https://github.com/Joconnell04/summit-air-agent/pull/96) | 055 |
| Simulated calls saved as audio; `make sim`, `make listen` | [#95](https://github.com/Joconnell04/summit-air-agent/pull/95) | 062 |
| End-of-turn wait measured and kept at 0.35 s; the longer digits wait no longer sticks for the rest of the call (about 0.55 s back on each later turn) | [#98](https://github.com/Joconnell04/summit-air-agent/pull/98) | |

Signing in: owner view and bench as `jackson`; the password is in the macOS Keychain (`security find-generic-password -a summit-air -s owner-admin-password -w`). The side model is on. Warm transfer and recordings are on.

## Still open

- **Last fix pass (round 3 lane):** the agent slips still seen in eval runs (re-asking given details, an apartment building declined, an invented fee line, a repeated 911 line, flagging instead of booking after hours, warming advice for a baby), the after-midnight "tomorrow" bug, and two at-risk phrasings ("just me at home", "a cane"). Then one eval run with two samples per scenario becomes the reference. Main's eval check is red only on that stale reference until then. Deploys when it lands.

## Decided without Jackson

- **Eval measurement moves to two samples per scenario.** Five single-sample runs on nearly the same code scored 91, 96, 90, 90 and 85%, so about plus or minus 5 points is noise. If the two-sample rate sets the floor below 90%, it's a change in measurement, not in quality.
- **One reference run per batch, not per PR.** Every merge that touches prompts, company settings or scenarios made the reference stale at about $1.70 a run.
- **The side model was switched off, checked, and switched back on.** The evals had never run with it; with and without on 26 scenarios, it raised no false alarms in 46 calls (decision 056).
- **Speaking rate:** review caught false triggers ("you don't need to speak slower", "send someone faster", "my heat is slow, I live on Main Street"); fixed before merge.
- **Accounts:** an old deploy with only the shared token would have locked everyone out; it now says exactly what to set. A caller who objects to recording is never recorded, consent switch on or off.
- **Cold handoff:** "Can I talk to someone now?" no longer triggers it; it needs "right now", "right away" or a repeated request. Swearing about the furnace doesn't count as anger at the call. A gas caller who demands a person after the safety lines is handed off (supersedes decision 051's "never on a safety call").
- **Debug bench:** phone numbers keep their last four digits; the full number never appears in a URL or a replay file. The publish gate runs the must-pass evals only, not the two-caller race.
- **ZIP rule:** a phone number containing the ZIP's digits doesn't count as the ZIP.
- **Recording-notice wording** lives in the company folder, not app code.
