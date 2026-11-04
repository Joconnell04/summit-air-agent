# Resume here

Where the work stopped when Jackson closed the laptop. To continue, tell the coordinating session "resume from resume-here". It has two hours to finish, check, and then tell Jackson the live calls are ready.

## State

- Merged and deployed: #132 (card numbers, dead air, sparks, water, breaker), #134 (moved address, identity check, heads-up, other contact number, two accounts), #135 (read-back corrections, ended windows, declines, two jobs, technician notes), #136 and #137 (a stale eval reference warns instead of failing CI).
- Open: #139, the final fix PR on `joco/must-pass-fixes` (booking-flow lane, `local_0880980d-ea54-470c-9f94-fcef45478356`). It has the server-spoken "top priority" line and the ZIP fallback when the address lookup finds nothing. Its resume note is `side-session-notes/must-pass-fixes-resume.md` in that lane's worktree.
- Open: the Retell Conversation Flow lane (issue #133, `local_61d1a46d-e412-4f1c-94ca-a5930cd9d46a`), resume note `side-session-notes/retell-flow-resume.md`.
- The eval reference is stale until #139's two-sample standard run is adopted. CI warns rather than fails (decision 086).

## Pause state (checked by the coordinator before sleep)

Both lanes were mid-run when the laptop closed, so the coordinator saved their state itself:

- **Booking-flow lane** (`joco/must-pass-fixes`, PR #139, worktree `.claude/worktrees/laughing-payne-7a256f`). Its latest commit, 00ca5df, is pushed: the reschedule fix ("a tool the worker spoke for asks for no reply; tool talk and confirm-the-address questions are never spoken"). It had not yet read the four review findings above; they're queued as a message. Running at sleep: a targeted eval (`make eval TAGS=id:known-caller-moves-visit,id:reschedule-own-booking,id:asks-agent-to-call-911,id:caller-declines-safety-advice,id:safety-override-insists,id:gas-downplayed-wants-booking SAMPLES=1`) and `pytest -q`. Treat both as cut off and rerun them after the review fixes. Snapshot: `wip/must-pass-fixes` (891c3d3).
- **Retell flow lane** (`joco/retell-flow-0ea78a`, worktree `.claude/worktrees/retell-flow-0ea78a`). The branch had never been pushed and is now pushed (5 commits ahead of main). It got the pause message. Running at sleep: a full two-sample standard run on the flow agent (`evals.run --suite standard`). Its finished smoke file is `evals/results/20261104-215403-retell_flow-fast-custom.json`, uncommitted, and saved in the snapshot `wip/retell-flow` (81caf35). Retell-side objects (the flow and agent) are left as they are. Check its spend before rerunning, since part of the standard run was paid and is lost.
- If a lane's process is still alive on wake, let it finish. If it died, rerun from the commands above.

## To do, in order

1. Booking-flow lane, on #139. First the four blocking review findings:
   - the top-priority line plays twice when `find_slots` is called twice in one turn; track it in the turn, not the transcript;
   - it can play before the safety lines; withhold `spoken_first` while `safety_state` is `signal` or `checking`;
   - with no ZIP said, the lookup path asks for the ZIP up to four times; ask once;
   - a street the model made up is accepted once any ZIP is said (`app/tools.py` around line 1288); require a house number the caller said.
   Non-blocking: restrict `_split_run_on` to digit-by-digit runs; update `prompts/tool-use.md:9`; add a worker test. Then:
   - fix the two reschedule bugs from the last test call (tool talk spoken: "The reschedule ran without a confirmation message"; a double confirmation; a stacked address and number question before the read-back);
   - finish the targeted and must-pass runs at two samples;
   - run the two-sample standard run and adopt it (cap $15).
2. Review #139 with a targeted re-check, merge, deploy (check no call is live), and run a scripted test call from the server. Fix anything it shows.
3. Tell Jackson the calls are ready, in chat:
   - ten bullets, one per call: who to play, what to say, what to listen for. Cover the new paths: no ZIP given, sparks, pushing for priority, a card number, stepping away, a correction after the read-back, the moved address, cancelling Pat Rivera's 142 Main Street visit from his cell (unknown number), plus noise, cutting in and long pauses from `evals/live-calls.md`;
   - the feedback worth speaking to afterwards, mapped to `docs/tuning.md`;
   - update `evals/live-calls.md` to match.
4. Publish an updated artifact with three interactive diagrams, based on the current code:
   - the path of a call, stage by stage;
   - the layering of rules (`docs/architecture.md`, "How rules are layered");
   - one more, such as where reply time goes (`docs/latency.md`) or how a change is tested and shipped.
   Load the artifact skills first.
5. Retell flow lane: continue from its note. If it passes every must-pass check and comes within 5 points, put it on (678) 336-6730 and give Jackson the number to compare.
6. Update `docs/ai-process/brief-next.md`, and run `make lanes`.

## Known, not blocking

- The first words of a line are sometimes clipped ("Have your home at ..." for "I have your home at ...").
- Re-asking known details is the most common grader miss; the plan is exploration 016 (stage-scoped prompts), after the live calls.
