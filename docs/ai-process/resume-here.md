# Resume here

Where the build stopped when Jackson closed the laptop (phase 3, after eval round 2). To continue, tell the coordinating session "resume from docs/ai-process/resume-here.md".

## Open PRs and what each needs

| PR | What | State | Next step |
|---|---|---|---|
| #69 | Warm transfer and call recordings | Two review blockers sent back (server must decide urgency, no silence if a transfer fails); lane was finishing | Re-review the fixes, merge, set `TRANSFER_OFFICE_NUMBER` and `TRANSFER_ON_CALL_NUMBER` in the server `.env` to Jackson's cell (never in the repo), document that the demo transfer rings his personal phone, `/round` spec and call-journeys for transfer and recordings, set up a recording bucket (proposed: free Supabase storage, `RECORDING_S3_*`) |
| #71 | AI check for owner guidance (replaces the word list) | Approved; conflicts with main; lane resolving plus three small fixes | Merge, record as DECISIONS.md 039 (supersedes the keyword part of 032), then tell the eval-floor lane |
| #72 | Must-pass reliability, eval floor 90%, passing demo version | Approved | After #71 merges: re-adopt the standard run (about $0.70), fix the journeys and safety prompt line, merge, record the floor decision |
| #73 | Technician summary (S-16) | Lane resolving conflicts and running `make check` | Review, merge, record decisions |
| none yet | Bake-off follow-up: verdict guard, shorter endpointing run, ElevenLabs read-back fix, latency doc | Lane working | Review, merge, record decision; if ElevenLabs reads back cleanly, decide the real-call finalists |
| none yet | Round 2 part A: residential definition, approval callback, booking on safety calls, burning-dust check | Lane working | Review, merge, decisions, `/round` logs |
| none yet | Round 2 part B: other-address booking, same-day repeat calls, time ranges, spelling, new scenarios | Lane working | Review, merge, decisions |

Lane sessions are linked from the coordinating session; each has its own worktree branch.

## Waiting on Jackson

1. Eval co-authoring round 3 of 3, `evals/rounds/03-judge-labels.md` (being drafted): mark 10 transcripts pass or fail with a line why.
2. The 10 live calls in `evals/live-calls.md` to (646) 624-0418 (#9), after the round 2 changes and the endpointing change are deployed.
3. A yes or no on a free Supabase storage bucket for call recordings.

## After everything merges

- Deploy (check no call is live first), with `SIDE_MODEL=openai-decisions` already set in the server `.env`.
- Load the rules page demo history on the server (`make owner-demo` equivalent for the server database).
- Reply-delay targets: add about 0.4 s to `make trace` numbers (the worker times from after its silence wait, DECISIONS.md 038).
- Phases 4 and 5 stay paused until Jackson picks them up (docs/ai-process/brief-phase-3-onward.md lists the open items).
