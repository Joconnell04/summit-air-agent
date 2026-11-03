# Brief: what's left for Jackson, phase 3 onward

Each item below needs Jackson's answer or time. Each has a recommendation, so "go with it" is a full answer. Phase 3 items come first, in the order they block work; then a head start on phases 4 and 5.

## Phase 3: test and tune

1. **Tonight's calls.** Make the 10 scripted calls in `evals/live-calls.md` to our number, (646) 624-0418. This is the go/no-go in #9. The safety fixes, the local names and the softer sign-off are live on the server. Recommend leaving Retell's 10 calls until the audio rig picks the final two, so you call twice, not three times.
2. **Listen to the name clips.** The 30 clips are in `data/name-clips/`; each name has a version ending `-plain.wav` and one ending `-dict.wav`. Is each name said the way a New Yorker says it? Two things to listen for:
   - With the pronunciation list, Houston Street, Kosciuszko, Schermerhorn and Conselyea run 1 to 1.5 s longer, so there may be a pause around the name.
   - Deepgram still heard "Todt Hill" as "Tote Hill" (MEASURED, PR #63).
3. **Must-pass scenarios that fail at random.** The rules page's three gate runs failed five different must-pass scenarios, once each and never twice. Two of them were safety scenarios. The edits under test only changed guidance, so most of these failures are noise, either from the simulated caller or from the agent behaving differently each run.
   - Recommendation: before the eval floor is set, run each must-pass scenario 3 times on the defaults (about $1.20, ASSUMED). Fix the scenarios or the agent until all 3 runs pass, then set the floor.
   - Question: is "passes 3 of 3" the bar for a safety scenario? Recommend yes.
4. **The technician summary.** Spec S-16 says the technician's job notes open with a short summary written after the call. The code only marks it as pending.
   - Recommendation: build it before the demo, about half a day (ASSUMED). It's the one spec line with no code behind it.
5. **The edit login on the rules page.** The edit login lasts 30 days and has no limit on wrong guesses, the same as the owner login. Recommend adding both to `docs/later.md` as fine for a demo.
6. **The bake-off result.** The audio rig is running now. Its readout will name the two stacks that get your real calls.
7. **Walking `docs/later.md`.** Phase 3 ends with each item marked done, after V1, or dropped. My calls:
   - Spanish: after V1.
   - Side model: after V1, unless the evals find a bottleneck.
   - Model matrix: run the cheap version (Haiku against Sonnet) during phase 3.
   - Playbooks per call: drop. The rules page now covers guidance by stage.
   - Warm transfer: after V1.
   - Production capacity: after V1.
   - Booking texts: after V1.
   - Reply-delay targets and the ASSUMED latency values: done once tonight's calls are traced.
   - Eval floor: after item 3.
   - Call recordings: after V1.
8. **Eval co-authoring rounds 2 to 5** (exploration 008, section 7). Four sessions of 10 to 20 minutes each, reading failures as in round 1. Recommend round 2 after the floor run in item 3.

## Phase 4: deploy and demo prep, a head start

9. **What the demo shows, and in what order.** Recommend:
   - Paul calls live.
   - The owner view shows that call arriving.
   - The rules page shows a guidance edit blocked by the eval gate and a later version that passed.
   - The bake-off scoreboard backs up why we built our own stack.

   Question: should Paul call cold, or have a script to work from?
10. **Interview drills.** `phases.md` lists planted-bug drills, but you said you'd rather prepare with feasibility questions. Recommend replacing that line with a list of "how would you do X" questions. The prep notes stay outside this repo.
11. **The rehearsal.** "A skeptical owner" could be you playing Paul on a call, or a Claude-played owner reading the owner view and asking questions. Recommend one of each.
12. **The backup number.** The watchdog already moves the number to a recorded fallback line if the worker fails. Question: is that enough, or should a second live stack (Retell) answer instead? Recommend the recorded fallback; it is simpler and already tested.

## Phase 5: handover, a head start

13. **How Revin gets the repo.** A private repo with Revin's GitHub users invited, or made public for the review window. Recommend private with invites; that needs their GitHub usernames.
14. **What stays in the repo.** The AI-process folder (co-authoring log, transcripts, briefs) is part of the case study. Recommend keeping it all, with one read-through for anything personal before sharing.
15. **Running costs while they review, and shutting down after.** Recommend leaving the server, number and voice services on through the interview and a week after (about $20 a month for the server, SOURCE: DECISIONS.md 005), then shutting everything down from a checklist.
