# Resume here

Where the build stopped when Jackson closed the laptop. Every working lane was told to push its work-in-progress and stop (phase 3, after eval round 2). To continue, tell the coordinating session "resume from docs/ai-process/resume-here.md".

## Open PRs and what each needs

| PR | What | State | Next step |
|---|---|---|---|
| #69 | Warm transfer and call recordings | Review fixes pushed; conflicts with main | Resolve, re-review, merge, deploy. Server `.env` already has `TRANSFER_OFFICE_NUMBER` and `TRANSFER_ON_CALL_NUMBER` (Jackson's cell, for the demo; never in the repo) and the five `RECORDING_S3_*` values (Supabase project `summit-air`, bucket `recordings`, upload tested). Document that the demo transfer rings Jackson's personal phone; `/round` spec and call-journeys for transfer and recordings |
| #72 | Must-pass reliability, eval floor 90%, passing demo version | Approved; conflicts with main | Resolve, re-adopt a standard run (about $0.70, #71 is merged), merge, record the floor decision |
| #73 | Technician summary (S-16) | Mergeable; lane was fixing a failing hook test | Check make check, review, merge, record decisions |
| #74 | Round 2 part A (draft) | Code and unit tests done; paid evals not run | Run its scenarios and the must-pass set (under $3), review, merge |
| #75 | Round 2 part B (draft, saved by the coordinator before a pause) | Code done; paid evals and final make check not run | Same as #74 |
| none | Bake-off follow-up (verdict guard, shorter endpointing, ElevenLabs read-back fix) | Lane was running a capped rig run when the app was force-quit; worktree `jolly-tharp-99a196` has no remote branch yet | Ask the lane to push and open its PR |

Merged and cleaned up: #70, #71 and the earlier fix branches; their six worktrees were removed.

## Waiting on Jackson

1. Eval co-authoring round 3 of 3, `evals/rounds/03-judge-labels.md` (ready): mark 10 transcripts pass or fail with a line why.
2. The 10 live calls in `evals/live-calls.md` to (646) 624-0418 (#9), after the round 2 changes and the endpointing change are deployed.
3. Done: recordings storage is set up. Old note: Supabase project `summit-air` (ref `zrcfodooibsxvnuxeylp`, org LeaperDev, us-east-1, free plan) has a private `recordings` bucket. Missing: an S3 access key. The Supabase access token in the Keychain (`supabase-access-token`) can read projects but is refused for secrets and project creation, so create the key in the dashboard (Project Settings, Storage, S3 access keys), then put the five `RECORDING_S3_*` values in the server `.env` only (endpoint `https://zrcfodooibsxvnuxeylp.storage.supabase.co/storage/v1/s3`, region `us-east-1`, bucket `recordings`). Call data stays in SQLite on the server.

## After everything merges

- Deploy (check no call is live first), with `SIDE_MODEL=openai-decisions` already set in the server `.env`.
- Load the rules page demo history on the server (`make owner-demo` equivalent for the server database).
- Reply-delay targets: add about 0.4 s to `make trace` numbers (the worker times from after its silence wait, DECISIONS.md 038).
- Phases 4 and 5 stay paused until Jackson picks them up (docs/ai-process/brief-phase-3-onward.md lists the open items).
