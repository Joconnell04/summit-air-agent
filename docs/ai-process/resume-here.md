# Resume here

Where the build stopped when Jackson closed the laptop. Every working lane was told to push its work-in-progress and stop (phase 3, after eval round 2). To continue, tell the coordinating session "resume from docs/ai-process/resume-here.md".

## Open PRs and what each needs

| PR | What | State | Next step |
|---|---|---|---|
| #69 | Warm transfer and call recordings | Review fixes pushed, CI running; needs a re-review | Re-review the fixes, merge, set `TRANSFER_OFFICE_NUMBER` and `TRANSFER_ON_CALL_NUMBER` in the server `.env` to Jackson's cell (never in the repo), document that the demo transfer rings his personal phone, `/round` spec and call-journeys for transfer and recordings, set up a recording bucket (proposed: free Supabase storage, `RECORDING_S3_*`) |
| #71 | AI check for owner guidance | Merged, DECISIONS.md 039 | Tell the eval-floor lane #71 is in |
| #72 | Must-pass reliability, eval floor 90%, passing demo version | Approved | After #71 merges: re-adopt the standard run (about $0.70), fix the journeys and safety prompt line, merge, record the floor decision |
| #73 | Technician summary (S-16) | Lane resolving conflicts and running `make check` | Review, merge, record decisions |
| none yet | Bake-off follow-up: verdict guard, shorter endpointing run, ElevenLabs read-back fix, latency doc | Lane working | Review, merge, record decision; if ElevenLabs reads back cleanly, decide the real-call finalists |
| none yet | Round 2 part A: residential definition, approval callback, booking on safety calls, burning-dust check | Lane working | Review, merge, decisions, `/round` logs |
| none yet | Round 2 part B: other-address booking, same-day repeat calls, time ranges, spelling, new scenarios | Lane working | Review, merge, decisions |

Lane sessions are linked from the coordinating session; each has its own worktree branch.

## Waiting on Jackson

1. Eval co-authoring round 3 of 3, `evals/rounds/03-judge-labels.md` (ready): mark 10 transcripts pass or fail with a line why.
2. The 10 live calls in `evals/live-calls.md` to (646) 624-0418 (#9), after the round 2 changes and the endpointing change are deployed.
3. Done: Jackson said yes to a Supabase storage bucket for recordings. Setup: a private bucket in his Supabase project plus an S3 access key from Storage settings (the key is created in the dashboard; put the five `RECORDING_S3_*` values in the server `.env` only). Keep the call database on SQLite for now (see the chat note on the database setup); revisit Postgres only if calls outgrow one server.

## After everything merges

- Deploy (check no call is live first), with `SIDE_MODEL=openai-decisions` already set in the server `.env`.
- Load the rules page demo history on the server (`make owner-demo` equivalent for the server database).
- Reply-delay targets: add about 0.4 s to `make trace` numbers (the worker times from after its silence wait, DECISIONS.md 038).
- Phases 4 and 5 stay paused until Jackson picks them up (docs/ai-process/brief-phase-3-onward.md lists the open items).
