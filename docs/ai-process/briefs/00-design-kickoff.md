# Design session kickoff

You are running the design phase of the Summit Air agent with Jackson. He talks, mostly through voice transcription; you drive.

## Read first

1. CLAUDE.md (build standard, glossary, working agreements)
2. docs/assignment.md (verbatim; reread before any scope call)
3. docs/ai-process/phases.md (the route; you keep its boxes current)
4. docs/considerations.md
5. docs/history/from-other-projects/README.md (lessons already learned; don't relearn them)
6. DECISIONS.md and the open issues (`/next`)

## How to run it

- Work through phase 1 in order. Start with users: write version 1 of docs/users.md, show it, ask for feedback.
- Each high-touch doc goes through `/round` until Jackson says lock. Log every round.
- When a step closes: tick it in phases.md, move its issue to Done on the board, commit, and say in one line "Unless there's anything else, next is <step>." Then wait for go.
- When Jackson raises a new idea mid-step, add it with `/consider` and keep going. If it needs research, `/explore` it in the background.
- Decisions: lay out options and criteria, recommend one, let Jackson decide, record it in DECISIONS.md, close or update the issue.
- Ask only questions whose answer changes what you write next, one at a time, with your recommended answer.
- Keep chat replies short; the substance goes in the files.

## What the system looks like (starting point, not decided)

Caller, voice stack, an integration layer (the tool API the agent calls), and a mocked field service system (FSM) shaped like ServiceTitan. We build the first three and the mock.

## Done

Phase 1 exit condition in phases.md is met, briefs for the build lanes are merged, and transcript excerpts are exported. Then say what phase 2 needs and stop.
