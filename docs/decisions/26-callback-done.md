# 26. How a person marks a callback done

Issue: #26. Status: ready

## Question

The owner view counts callbacks promised against done (C-45, spec S-32), but brief 06 makes the page read-only. Today a callback only becomes done when the system places it itself and the caller answers (journey 12). Every callback a person owes (a complaint, a billing question, the on-call technician's call back) ends up overdue and flagged, even when the office returned it. How should a returned callback be recorded?

## Options

### A. A Done button on the owner view
One small write behind the same token: the person who returned the call clicks Done on its row. Simple, and it shows well in the demo. The page is no longer read-only, and the number is only as good as people's clicking.

### B. Read it from ServiceTitan
A background job marks a callback done when ServiceTitan logs an outbound call to that number after the callback was promised. No new screen, and it measures what happened rather than what someone clicked. It depends on the office calling from a line ServiceTitan tracks, and needs the number to be on a customer record.

### C. Leave it for V1
Only the system's own callbacks count as done. Honest, but every person callback shows as overdue, which makes the callbacks number look worse than the office is.

## Context

| | A. Button | B. From ServiceTitan | C. Leave it |
|---|---|---|---|
| Build effort | Small: one endpoint, one button, a test | Small to medium: one job, one adapter read, tests | None |
| What it measures | What someone clicked | Calls actually placed | Only system calls |
| Works if the office calls from a cell phone | Yes | No | No |
| Owner view stays read-only | No | Yes | Yes |
| In the demo | Paul sees a familiar control | A number that moves on its own | A red number to explain |

The mock ServiceTitan already serves the Telecom calls list with a direction field (`servicetitan_mock/routes/settings.py`), and the adapter already reads it (`FsmAdapter.calls`), so B needs no adapter change.

## Recommendation

B, with A as the fallback if Summit Air's office doesn't place calls through ServiceTitan. B keeps the page read-only and counts calls that were really made, which is what the owner is asking when they ask whether callbacks happen.

## Research notes

- ServiceTitan's Telecom API lists calls with a direction (Inbound or Outbound) and the customer they were matched to; our mock models the same shape (SOURCE: `servicetitan_mock/routes/settings.py`, `shape_call`).
- Until this is decided, `scripts/seed_owner_day.py` marks the callbacks the office returned as done directly in the database, so the demo page reads like a normal day. That stand-in is labelled in the script.
