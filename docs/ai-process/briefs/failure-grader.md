# Build brief: failure grader for finished calls

Branch: `joco/failure-grader`, from `joco/stripped` (PR #164, 28707de). Issue #178, label `agent`. The pull request says `Closes #178`.

## Goal

A safety net the owner can trust: once a day, or when someone asks, an Opus 5.5 grader reads finished calls and flags only the calls where something really went wrong. A true failure becomes an owner-facing flag on the Today page, with a one-sentence reason and the lines of the call that show it. A false flag is worse than a missed style issue, so the grader is tuned for precision, and the server drops any flag whose evidence it can't find in the call.

Read first: CLAUDE.md, docs/assignment.md, docs/users.md (the owner is not technical), docs/design-system.md, DECISIONS.md 119 (the done bar and what a safety error is), `evals/rounds/side-by-side-01.md` on `joco/side-by-side-014a4c` (the calls the calibration set comes from).

## What counts as a true failure

Only these five, each with the evidence quoted from the call:

1. The caller was hung up on or cut off before they were done.
2. A booking or callback was wrong or missing when one was promised: wrong day or time, wrong address or number, or nothing stored.
3. A real hazard where the safety advice was not spoken, or a retraction that was ignored.
4. The agent told the caller something false or unsafe.
5. The CRM records contradict what the caller was told.

Style never counts: wording, an extra question, saying the caller's name, narrating a step, a reworded server line, repeated safety advice. Earlier eval rounds found our automated checks lean oversensitive (memory: safety checks oversensitive), so the prompt names these as not failures and the calibration set holds style-only calls that must not be flagged.

## Scope

- **Grader** (`app/failure_grader.py`, prompt `prompts/failure-grader.md`). For one ended call it reads the Retell call (`GET /v2/get-call/<id>`, `transcript_with_tool_calls`) when the call came through Retell, or the server's own turns otherwise, plus the server's rows for the call: bookings, callbacks, flags, leads, do-not-call and notes. One model call with a JSON schema answer: `failure`, `kind`, `severity` (critical or serious), `reason`, `evidence`. The model is the `offline.grader` profile in `config/models.yaml`. The server keeps a failure only when it names a kind and every evidence line is found in the call's text or rows.
- **Cache** (table `grades`). One row per call id. A graded call is never sent again unless forced; the raw model answer and the cost are kept for the bench.
- **Runs** (table `grader_runs`). A run covers one call or a time range, runs on a background thread (one at a time), and records progress, flagged calls and spend, so the pages can show progress while it works.
- **Schedule** (table `grader_schedule`, history kept like `setting_changes`). Off, or daily at a local time; daily at 6:00 AM by default, covering calls since the last scheduled run. The jobs loop starts the run when it is due and does not wait for it.
- **Flags.** A kept failure adds a flag of kind `grader`: Urgent for critical, High for serious. It shows wherever flags already show. Grader flags are never emailed or texted, enforced in `jobs.alert_on_call`.
- **Owner view.** A Checks page: "Check calls from" past hour, past day or a custom range, a cost estimate before a run that covers many calls, progress while it runs (a bar with the count, no bare spinner), and the flagged calls with reason and evidence, each linking to its call. "Check this call" on the call page. The schedule on the Settings page (admin only to change).
- **Bench.** The same range controls, every grade with the raw model output, and Force to grade a call again.
- **Calibration** (`evals/failure_grader/`). About 20 real calls from the side-by-side round with my own labels, read from the transcripts: true failures (the hang-up after "no thanks", the callbacks stored on the wrong day) and style-only calls. A script grades them and reports precision and recall. Target: no false flags on the style-only calls. Spend under $5, logged.

## Out of scope

- Grading calls in progress, or acting on a grade beyond the flag (no texts, no emails, no automatic fixes).
- Any change to `app/tools.py` or `voice/retell/bridge.py`, which the server lane owns.
- Deploying or touching live services. The coordinator deploys.

## Done

`make check` passes, the PR is open with the calibration numbers in its description, and the coordinator has the PR link and how to try it in the owner view.
