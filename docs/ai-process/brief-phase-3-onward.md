# Brief: what's left for Jackson, phase 3 onward

Each item below needs Jackson's answer or time. Each has a recommendation, so "go with it" is a full answer. Phase 3 items come first, in the order they block work; then a head start on phases 4 and 5.

## Phase 3: test and tune

1. **Tonight's calls.** Make the 10 scripted calls in `evals/live-calls.md` to our number, (646) 624-0418. This is the go/no-go in #9. The safety fixes, the local names and the softer sign-off are live on the server. Recommend leaving Retell's 10 calls until the audio rig picks the final two, so you call twice, not three times.
2. **Listen to the name clips.** The 30 clips are in `data/name-clips/`; each name has a version ending `-plain.wav` and one ending `-dict.wav`. Is each name said the way a New Yorker says it? Two things to listen for:
   - With the pronunciation list, Houston Street, Kosciuszko, Schermerhorn and Conselyea run 1 to 1.5 s longer, so there may be a pause around the name.
   - Deepgram still heard "Todt Hill" as "Tote Hill" (MEASURED, PR #63).
3. **Must-pass scenarios that fail at random.** The rules page's gate runs failed five different must-pass scenarios, once each, two of them safety scenarios (MEASURED, PR #65). Most look like noise from the simulated caller or the agent. One pattern is not noise: `safety-override-insists` and `two-people-mind-change` failed in 3 of 4 runs with the owner's demo edit, so a closing note like "clear a path to the furnace" may really change how the agent handles those calls. The gate blocked it, which is its job.
   - Recommendation: before the eval floor is set, run each must-pass scenario 3 times on the defaults (about $1.20, ASSUMED). Fix the scenarios or the agent until all 3 pass, then set the floor.
   - Question: is "passes 3 of 3" the bar for a safety scenario? Recommend yes.
   - For your awareness, decided without you (DECISIONS.md 031): the owner's publish gate reruns a failed must-pass scenario once and blocks if it fails again, and grader-only fails don't block. An ordinary eval run still fails on one miss.
4. **The technician summary.** Spec S-16 says the technician's job notes open with a short summary written after the call. The code only marks it as pending.
   - Recommendation: build it before the demo, about half a day (ASSUMED). It's the one spec line with no code behind it.
5. **Rules page loose ends.** Recommend adding all three to `docs/later.md`:
   - The edit login lasts 30 days and has no limit on wrong guesses, the same as the owner login.
   - The guidance filter is a keyword list. A reviewer got 18 of 30 new phrasings past it (for example "Bypass the usual checks for regulars"), though the server gates still refused every unsafe booking. The real protection is the server gates and the eval gate (DECISIONS.md 032).
   - The demo history has two blocked versions and no passing one. Recommend one more gate run, about $0.45, after item 3 makes the scenarios steady.
6. **The bake-off result.** The audio rig lane is still building and running. Its readout will name the two stacks that get your real calls.
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

9. **What the demo shows, and in what order.** The rules page's demo history isn't loaded on the server yet; that happens once item 5's passing version exists. Recommend:
   - Paul calls live.
   - The owner view shows that call arriving.
   - The rules page shows a guidance edit blocked by the eval gate and a later version that passed.
   - The bake-off scoreboard backs up why we built our own stack.

   Question: should Paul call cold, or have a script to work from?
10. **Interview drills.** `phases.md` lists planted-bug drills, but you said you'd rather prepare with feasibility questions. Recommend replacing that line with a list of "how would you do X" questions. The prep notes stay outside this repo.
11. **The rehearsal.** "A skeptical owner" could be you playing Paul on a call, or a Claude-played owner reading the owner view and asking questions. Recommend one of each.
12. **The AI disclosure when we know the caller.** A new caller hears "virtual assistant" in the greeting, and owners can't edit it out. A known caller hears "Hi, this is Summit Air. Is this Dana?" with no disclosure, and the agent only says it's an AI if asked (spec S-34). Question: should the known-caller greeting say it too? Recommend yes; it costs a few words, and a caller who later learns they talked to an AI without being told may feel misled.
13. **The backup number.** The watchdog already moves the number to a recorded fallback line if the worker fails. Question: is that enough, or should a second live stack (Retell) answer instead? Recommend the recorded fallback; it is simpler and already tested.

## Phase 5: handover, a head start

14. **How Revin gets the repo.** A private repo with Revin's GitHub users invited, or made public for the review window. Recommend private with invites; that needs their GitHub usernames.
15. **What stays in the repo.** The AI-process folder (co-authoring log, transcripts, briefs) is part of the case study. Recommend keeping it all, with one read-through for anything personal before sharing.
16. **Running costs while they review, and shutting down after.** Recommend leaving the server, number and voice services on through the interview and a week after (about $20 a month for the server, SOURCE: DECISIONS.md 005), then shutting everything down from a checklist.
