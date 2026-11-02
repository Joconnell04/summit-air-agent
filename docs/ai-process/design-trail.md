# Design trail

How the design was reached, in order, so a reader can follow each choice back to the reasoning behind it. Each step links to the file that holds the detail. Updated whenever a decision lands.

## How a choice gets made

1. An idea or concern goes into `docs/considerations.md` (by voice, with `/consider`).
2. If it needs evidence, a background research agent writes `docs/explorations/NNN-*.md`.
3. If it's Jackson's call, a short readout in `docs/decisions/NN-*.md` lays out the options, trade-offs and a recommendation. Jackson reads it beside the chat and answers.
4. The answer becomes one entry in `DECISIONS.md` (what, why, what else was considered, what would change it), the issue is closed, and the docs it touches are updated.
5. Anything set aside on purpose goes in `docs/later.md`.
6. Documents that define the product (users, call journeys, spec) change only through logged rounds in `coauthoring.md`, with Jackson's feedback verbatim.

## Steps so far

| Step | Outcome | Where |
|---|---|---|
| Users | Ten caller types, staff roles from research, how the agent tells callers apart, safety and repeat-caller handling; locked after three rounds | docs/users.md, coauthoring.md (users r1 to r3), explorations 001 and 002 |
| Working method | Decisions surfaced one at a time as readout files; ideas Jackson raises mid-step are looked into, not written in as settled | CLAUDE.md, docs/decisions/README.md |
| Eval design | Our own harness, code checks decide pass or fail, an LLM grader scores quality | DECISIONS.md 002, docs/decisions/06-eval-design.md |
| Mock ServiceTitan | Built and merged: technician-window slots, idempotent booking, tenant config | DECISIONS.md 003, brief 01, pull request 14 |
| Voice platform | Our own LiveKit stack, with Retell as the baseline to beat | DECISIONS.md 004, docs/decisions/04-voice-platform.md, explorations 003 and 004 |
| Server | A new Vultr server in New Jersey, separate from the shared one | DECISIONS.md 005, docs/decisions/16-server.md |
| Pipeline and models | Three separate steps, a right-sized model per job, no side model at launch, least-privilege harness | DECISIONS.md 006, docs/decisions/05-pipeline-and-models.md, docs/architecture.md |
| Bake-offs | Hypotheses and pass rules set before any run | docs/bakeoffs.md |
| Phone number | (646) 624-0418 bought for our stack; the 678 Retell number is the bake-off baseline; one number after the bake-off | DECISIONS.md 004 |
| Texting | Left out of V1: not in the assignment, and registration takes weeks; terms pages kept for later | DECISIONS.md 007, docs/decisions/15-texting-registration.md |
| Scope | Every consideration triaged: privacy and success measures in, surge mode out (scaling plan instead), languages, decision models and playbooks later | DECISIONS.md 008, docs/decisions/10-considerations-triage.md |
| Call journeys | Shared spine, thirteen journeys including calls Summit Air makes, server checks named in each, Revin field practice cited; locked after three rounds | docs/call-journeys.md, coauthoring.md (call-journeys r1 to r3), exploration 005 |
| Spec | 51 testable statements with must-pass marked; assumptions in the brief; plans renamed Summit Premium and Standard; joining flow, tool-failure queue, do-not-call added; locked after two rounds | docs/spec.md, docs/brief.md, coauthoring.md (spec r1 and r2) |
