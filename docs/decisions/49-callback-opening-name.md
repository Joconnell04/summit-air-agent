# 49. Should the callback opening line ask for the caller by name?

Issue: #49. Status: ready

## Question

A callback the system places counts as done only if the right person was confirmed on the call (DECISIONS.md 010). Today's opening line, "Hi, this is Summit Air calling you back, like we said we would.", never checks who answered, so most system callbacks will be tagged open and sent to a person. Should the line ask for the caller by name? It's agent wording, so the change goes through `/round`.

## Options

### A. Ask by name
"Hi, this is Summit Air calling back for {first_name}. Is that you?" A yes confirms the person, so a handled callback can count as resolved. It mirrors the known-caller greeting the agent already uses inbound.

### B. Keep the line, confirm later
Leave the opening as it is and rely on the read-back or a name check later in the call. Simpler, but many callbacks end before either happens.

### C. Leave it
Accept that system callbacks are mostly tagged open and a person closes them.

## Context

| | A | B | C |
|---|---|---|---|
| Callbacks the system can close | most | some | few |
| Privacy | names the person to whoever answers (first name only) | none | none |
| Work | one line and a round | none | none |

## Recommendation

A, with the first name only, falling back to today's line when no name is on file. It is how a person calling back would open, and it is what lets the owner view's "callbacks done" number reflect calls the system really closed.

## Research notes

- The rule and its tests: `app/outbound.py`, `tests/tool_server/test_ts_callback_done.py` (PR #46).
- The current line: `callback_opening` in `tenants/summit-air/agent.yaml`.
