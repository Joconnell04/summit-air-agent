# Brief: where things stand

The one page to read before the live calls and the interview: what needs Jackson, the decisions worth knowing cold, what was decided without him, and what's still being built. Older briefs are in [past-briefs/](past-briefs/).

## Needs Jackson

1. **The 10 live calls, whenever you're ready.** Call (646) 624-0418 with the script in `evals/live-calls.md`. Everything merged is deployed, and a scripted test call passed after the last deploy. While you listen, note:
   - The first words of a line may be clipped: "Good to hear from you" came through as "To hear from you", and the goodbye still splits around the hang-up.
   - Two questions in one turn slipped through once ("Is that the one you want to keep? Do you want a different day?").
   - If you ask for a person, the transfer rings your cell.
2. **One feedback pass after the calls.** Say what felt off, and I'll map each complaint to a setting in `docs/tuning.md` and try the obvious fix first. A lower turn-detector maximum is already queued.
3. **Call the Retell Conversation Flow version when it's ready.** If it passes every must-pass check and comes within 5 points of our stack, it goes on (678) 336-6730 for you to call and compare.

Nothing needs a top-up: no run has failed for credit. Paid eval runs since last night come to roughly $60 across all lanes, added from their reports, not from invoices. The three new lanes are capped at $6 each, plus about $4 for the shared reference run. The only extra Cartesia use was the Ink-2 test's $0.13; voice on test calls runs on the plan, so the overage stays far under your $20.

## Decisions to know cold

If someone asks why it works the way it does, these are the answers. Numbers are DECISIONS.md entries.

| Topic | What we decided | Entry |
|---|---|---|
| Platform | Our own LiveKit stack, with Retell as the baseline to beat; the audio bake-off kept ours (Retell paraphrased the gas lines on 5 of 6 calls) | 004, 038 |
| The core rule | The model asks; the server decides. Booking, refusals, hang-ups, priority and safety lines are server code, and a flag the model can set is not a gate | spec, from-other-projects |
| Live model | Haiku 5.5, thinking off; Sonnet for the technician summary; Opus grades the evals | 036, later.md C-57 |
| Ears and voice | Deepgram Nova-3 hears; Cartesia sonic-3 speaks. Ink-2 measured 0.6 s slower a turn and worse on street names; Cartesia's hosted agents can't let our server own the safety lines | 068, 065 |
| Safety posture | Escalate politely, don't coach; fixed safety lines spoken word for word by the server | 033, 060 |
| Priority | Set by the server from what the caller said and the real outside temperature; only goes up; 60 and over is an older adult (NYC Health); a caller's stated fact always counts, insisting alone never raises it, and a raise after the caller heard the wait is flagged for the dispatcher | 019, 072, 073 |
| ZIP and address | The server acts only on a ZIP the caller said, or one looked up from the street they said (NYC GeoSearch, exact match only) | 058, 069 |
| Handoffs | A caller who demands a person is transferred at once; calmer requests get a warm transfer with a summary | 051, 061 |
| Evals | Code checks decide pass or fail; the grader can fail a call but never rescue one; the floor is 90%, measured at two samples per scenario. Today's reference: 92%, every must-pass case passing | 002, 042, 066 |
| Scope | No texting in V1 (A2P registration); Spanish waits (C-16) | 007, 034 |

## Signed off

The six new lines from the call-handling lane: the sparks line (edited to end "I can get a technician out to look at it," since the breaker isn't a condition for booking), the dead-air check-in and goodbye, the call-back greeting, the card-number line and the billing line (call-handling lane, #132).

## Decided without Jackson

Say if you disagree with any of these.

- **A caller's stated fact always counts.** "Tell them there's a baby in the house" raises priority even when it sounds like a request. A false raise is the safe direction, and the after-the-wait flag tells the dispatcher (073).
- **The heads-up to the number on file is a short automated call, not a text,** since texting is out of V1. In the demo it only dials numbers on an allowlist in the server's settings, so it never calls a made-up customer's number. Otherwise it records "would have called". SMS is the production version.
- **Card digits are never stored anywhere.** That's the one exception to "the bench shows everything as recorded" (064).
- **The eval floor stayed at 90%** when the address lane's run came in at 87%. The two agent gaps it found were fixed instead, and the next run came in at 92%.
- **Reviews now re-check each fix.** Three priority and safety changes in a row fixed one case and broke another ("No heat, baby in the house" dropping to High; "What do I do? I smell gas" losing the gas lines). Every fix gets a second targeted review before it merges.
- **Lane-level runs, one reference.** Each lane tests its own scenarios and the must-pass set; one standard run after a batch sets the reference.

## Still being built

Three lanes started from your approved list. Each has an issue, and progress is in [lanes.md](lanes.md).

- **Caller changes (#128):** a caller who moved (new address looked up, service area checked, CRM updated, open visits moved so nothing goes to the old address); a polite name-and-address check before changes from an unknown number, plus the heads-up; another callback number; a caller with two accounts.
- **Booking flow (#129):** a correction after the read-back resets it; a visit whose window has ended is never confirmed as upcoming; businesses that slipped past the decline; work Summit Air doesn't do; two jobs in one call; a requested technician becomes a note, never a promise.
- **Call handling (#130):** card numbers stopped and never stored, billing to an office callback; dead air checked in on every 2 minutes, a polite hang-up at 8 with a call-back invite and a resume on the next call; the sparks route; AC water through the ceiling is Urgent; a breaker that keeps tripping is High.

- **Retell Conversation Flow (#133):** our design mapped onto Retell's stage graph, with the safety lines as static sentences and our tool server still deciding every gate. Checked against the spec, then run on the must-pass set and the standard suite at two samples. A fair second test of whether a managed platform does this better.

After the first three merge: one two-sample standard run for the new reference, a deploy, and a scripted test call. The next prompt idea, giving the model only the current stage's instructions, is in [exploration 016](../explorations/016-stage-scoped-prompts.md).

## Live on the server

Everything merged is deployed. The bench shows calls as recorded. The round 3 final pass and the CO spelling fix, the address lookup with preferred names, and priority from the caller's own words are all live. The address lookup is switched on.

Signing in: https://45-77-105-247.sslip.io/owner (owner view) and https://45-77-105-247.sslip.io/bench (debug bench), both as `jackson`. The password is in the macOS Keychain (`security find-generic-password -a summit-air -s owner-admin-password -w`). The side model, warm transfer and recordings are on.

## Your earlier questions

Answered in [past-briefs/brief-after-check-ins.md](past-briefs/brief-after-check-ins.md): Cartesia's hosted agents, Cartesia for speech-to-text, why Cartesia speaks but doesn't hear, and how we pick a model for each job.
