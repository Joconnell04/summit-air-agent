# Summit Air inbound agent

An AI phone agent that answers inbound calls for Summit Air, a 40-tech residential HVAC company in NYC. It works out what's wrong, checks whether the caller is residential and a member, collects what a technician needs, flags urgent calls, and books the visit or sets next steps.

Status: design phase. The number, scoreboard and architecture go here as they land.

## Run

```
make setup    # install, enable the pre-commit hook
make check    # lint, tests, test-count ratchet
```

## Where things are

- `docs/assignment.md`: the assignment, verbatim
- `docs/brief.md`: assumptions made about it
- `docs/users.md`, `docs/call-journeys.md`, `docs/spec.md`: who it serves and what it must do
- `DECISIONS.md`: choices made and why
- `from-other-projects/`: lessons carried in from earlier agent builds
- `docs/ai-process/`: how this was built with AI (briefs, co-authoring log, transcripts)
- Work is tracked in GitHub Issues and the project board
