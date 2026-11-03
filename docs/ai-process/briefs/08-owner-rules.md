# Build brief: owner-editable rules by call stage

Branch: `joco/rules-by-stage-7503c4`. Depends on brief 07 (the owner view redesign and its design system). Issue #56, spec S-51, DECISIONS.md 022.

## Goal

The owner sees every rule the agent follows, sorted by where in the call it applies, each marked locked or editable, and can change a small, safe set of them: four wording lines and short guidance per stage. No screen can reach a gate.

Read first: CLAUDE.md, docs/assignment.md, docs/explorations/009-rules-by-call-stage.md (the design and the 28-rule inventory), docs/ai-process/ideas-round-questions.md questions 4 to 9, docs/design-system.md.

## What was decided (questions 4 to 9, all recommendations taken)

- A read-only rulebook first, then edits behind an eval gate: soft guidance per stage plus the four wording lines from exploration 009 (greeting, callback opening, anything else, goodbye).
- No screen ever loosens a gate or edits the safety script. Gates change only through engineers and `/round`. Guard the read-back's home check and the priority brackets with server-side validation and tests.
- A separate edit login; the owner token stays read-only.
- Edits live in a versioned SQLite table over the YAML defaults, with an audit trail and rollback. The server never writes to git.
- For the demo, a version already in the history with its gate result, not a live edit. One real gate run is approved if it costs under $1.
- The server decides the call stage, never the model.

## Scope

- **Rulebook** (`app/rulebook.py`, `/owner/rules`). Built from the tenant files, the prompt files and the live version each time, not kept by hand. Server checks are code, so their words live in the module, with tests that fail when a refusal code, a must-pass spec line or an agent.yaml section has no row.
- **Overlay** (`app/rules.py`, tables `rule_versions` and `call_stages`). Validation keyed to a fixed editable list; drafts, the gate, rollback as a new version; each call pinned to the version it started under.
- **Stage** (`rules.stage_now`). From the server's records: opening at start, booking once the agent looked for openings or read the address back, closing once a booking, change, lead or flag is done or the agent asked to end. Booking and closing guidance reach the model as a `[server]` note when the stage starts, never on a safety call.
- **Gate** (`evals/gate.py`, `make rules-gate`). Every must-pass scenario once, with the draft live in each scratch world. The result is written back on the version it tested.
- **Editor** (`/owner/rules/edit`). Fields for the editable lines and guidance, Save draft, Publish, Discard, and the version history with Roll back.

## Out of scope

Editable numbers and brackets (exploration 009 option B in full), condition and action rules (option D), and guidance for after the call, which waits for the technician summary. All three are in docs/later.md.
