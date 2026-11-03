# Summit Air inbound agent

An AI phone agent that answers inbound calls for Summit Air, a 40-tech residential HVAC company in NYC. It works out what's wrong, checks whether the caller is residential and a member, collects what a technician needs, flags urgent calls, and books the visit or sets next steps.

Status: build phase. The mock ServiceTitan, the tool server, both voice layers, the eval suite and the owner view are built; the owner view's design system is docs/design-system.md.

## Run

```
make setup    # install, enable the pre-commit hook
make check    # lint, tests, test-count ratchet
make seed-st  # rebuild the mock ServiceTitan world
make mock-st  # serve the mock ServiceTitan on :8101
make postman-st  # run the Postman collection against it with Newman
make server   # the tool server on :8100, talking to the mock on :8101
make postman  # seed a world, start both, run the tool server's collection with Newman
make trace CALL=<id>  # a call's timeline in plain text
```

## Where things are

- `docs/assignment.md`: the assignment, verbatim
- `docs/brief.md`: assumptions made about it
- `docs/users.md`, `docs/call-journeys.md`, `docs/spec.md`: who it serves and what it must do
- `DECISIONS.md`: choices made and why
- `from-other-projects/`: lessons carried in from earlier agent builds
- `app/`: the tool server every call talks to; see its README and `docs/tools.md`
- `servicetitan_mock/`: the mock ServiceTitan the agent's integration layer talks to; see its README. Company-specific parts live in `tenants/summit-air/`
- `docs/ai-process/`: how this was built with AI (briefs, co-authoring log, transcripts)
- Work is tracked in GitHub Issues and the project board
