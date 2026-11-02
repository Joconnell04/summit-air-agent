# Build brief: owner view and background jobs

Branch: `build/owner-view`. Depends on the tool server.

## Goal

What Summit Air's owner and dispatcher see, and the jobs that run between calls. This is the screen shown in the demo meeting.

Read first: CLAUDE.md, docs/users.md (owner, dispatcher), docs/spec.md (S-25, S-28, S-32, S-42, S-44, S-45, S-47), docs/call-journeys.md (journeys 2, 12, 14), docs/considerations.md (C-04, C-31, C-42, C-45), Jackson's design rules in ~/.claude/CLAUDE.md (dark, compact, minimal wording, no browser prompt or alert dialogs).

## Scope

- A read-only web page served by the tool server: today's bookings, flags with reasons, calls needing a person, callbacks promised against done, booking rate by call type, booked-and-kept, membership and other leads, out-of-area calls by ZIP, safety overrides and refused tool calls, each call's timeline linked to its trace. Behind a login token.
- Background jobs: offer earlier openings to flagged urgent bookings (journey 12), schedule requested callbacks, retry queued bookings, mark overdue callbacks, and the retention job (transcripts 30 days, call records 12 months; full logs in development).
- Email alerts to the on-call person after hours.

## Done when

`make check` passes with tests for each job and each owner-view number; the page renders from a seeded day of calls and is screenshot-checked at desktop size; the reviewer agent has checked the branch.
