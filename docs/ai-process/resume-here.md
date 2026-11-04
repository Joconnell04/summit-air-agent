# Resume here

Where the build stopped when Jackson closed the laptop. Every working lane was told to push its work-in-progress and stop (phase 3, after eval round 2). To continue, tell the coordinating session "resume from docs/ai-process/resume-here.md".

## Open PRs and what each needs

| PR | What | State | Next step |
|---|---|---|---|
| #82 | Eval round 3: wording, safety tone, grader standards, speech filter for read-back lead-ins | Lane merging main and running the standard suite (about $1.10) | Merge if it clears the 90% floor, deploy; if not, decide on the misses listed in the PR |
| none | Bake-off follow-up (verdict guard, shorter endpointing, ElevenLabs read-back fix) | Saved in worktree `jolly-tharp-99a196`, not pushed; its session is gone | Push it as a draft PR and finish it, or close it out |

Merged since the last note: #69 (warm transfer and recordings), #72 (eval floor 90%), #73, #76, #77, #79, #80, #81 (technician summary), #74 and #75 (eval round 2), #78. Deployed to the server after #69; a scripted test call booked a tune-up end to end. Known on that call and fixed in #82, not yet live: "Before I book, I need to read back the details", "Let me read that back", "Stay warm out there". Also noticed: the phone number is read as "six hundred and forty six ..." instead of digit by digit.

## Queued (start when the dependency lands)

- Side model extension ("asked for a person", "can't keep up"): after PR #83 merges. Exploration 014, idea 3.
- Second latency lane (lookup results up front, a short line before silent tool calls, bare-yes fast path in shadow mode): after PR #82 merges. Exploration 014, ideas 4 to 6.
- Debug bench at `/bench` on the owner view's server (call timeline, rule results, eval runs, the simulated-call listener, replay a call as a scenario), admin only: after the accounts lane merges.
- After everything merges: re-adopt one eval reference, deploy, one scripted test call, then Jackson's 10 live calls.

## Waiting on Jackson

1. Eval co-authoring round 3 of 3, `evals/rounds/03-judge-labels.md` (ready): mark 10 transcripts pass or fail with a line why.
2. The 10 live calls in `evals/live-calls.md` to (646) 624-0418 (#9), after the round 2 changes and the endpointing change are deployed.
3. Done: recordings storage is set up. Old note: Supabase project `summit-air` (ref `zrcfodooibsxvnuxeylp`, org LeaperDev, us-east-1, free plan) has a private `recordings` bucket. Missing: an S3 access key. The Supabase access token in the Keychain (`supabase-access-token`) can read projects but is refused for secrets and project creation, so create the key in the dashboard (Project Settings, Storage, S3 access keys), then put the five `RECORDING_S3_*` values in the server `.env` only (endpoint `https://zrcfodooibsxvnuxeylp.storage.supabase.co/storage/v1/s3`, region `us-east-1`, bucket `recordings`). Call data stays in SQLite on the server.

## After everything merges

- Deploy (check no call is live first), with `SIDE_MODEL=openai-decisions` already set in the server `.env`.
- Load the rules page demo history on the server (`make owner-demo` equivalent for the server database).
- Reply-delay targets: add about 0.4 s to `make trace` numbers (the worker times from after its silence wait, DECISIONS.md 038).
- Phases 4 and 5 stay paused until Jackson picks them up (docs/ai-process/brief-phase-3-onward.md lists the open items).
