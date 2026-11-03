# 26. How a person marks a callback done

Issue: #26. Status: decided and built

## Question

The owner view counts callbacks promised against done (C-45, spec S-32), but brief 06 makes the page read-only. Today a callback only becomes done when the system places it itself and the caller answers (journey 12). Every callback a person owes (a complaint, a billing question, the on-call technician's call back) ends up overdue and flagged, even when the office returned it. How should a returned callback be recorded?

## Decision

B, with an outcome on each call (resolved, still open, no contact); only a resolved outbound call after the promise marks the callback done, so a missed call or the wrong person answering can't. DECISIONS.md 010.

## How it is built

ServiceTitan's call has no outcome field: the only classification is `callType` (empty for outbound calls), a reason and tags, and the phone system creates the call (the API can only classify it). So the outcome is a tag.

- **Tag types, per tenant.** `tag_types` in `tenants/summit-air/config.yaml` (what ServiceTitan's tag-types list returns) holds "Callback resolved", "Callback open" and "No contact"; `fsm.callback_tags` in `agent.yaml` says which means what.
- **The office** puts one of the tags on the call it made, in ServiceTitan, the way it dispositions any call.
- **The server, on its own callbacks.** When the voice layer reports a dialed callback (`POST /outbound/{id}/done`), `outbound.finish` decides the outcome and applies the tag by finding the call by its `sid` and putting the tag on it (`PUT telecom/v2/.../calls/{id}`). It is **resolved** only if all three hold: a person answered (not a voicemail, no answer or a failed call); the person who asked was reached (the call's caller id is the number that asked, and the agent's read-back was confirmed, or the agent asked for the customer's name and the next caller line said yes); and the reason was handled on the call (a booking, reschedule or cancel went through, or the agent's `end_call` reason was `resolved` and the server saw the caller speak and no flag or further callback raised on the call). Anything else is **open** (answered but not confirmed or not handled, or the caller asked not to be called) or **no contact** (nobody answered). Open and no contact hand the callback to a person at once, as a failed dial did before.
- **The job.** `jobs.confirm_callbacks` reads each open callback's number in the call log (`phoneNumberCalled`) and marks the callback done when an outbound call after the promise carries the resolved tag, closing the flags the callback left on its call. A failed tag is retried a few times (`outbound.retry_tags`). One resolved call settles every open callback promised to that number before it.
- **The owner view** counts promised against done from the callback's status, which only the job sets, so it stays read-only.
- **In the mock** the phone system's part is `POST /debug/calls`, which is not in the real API; the adapter uses it only when the tenant sets `fsm.log_calls_via_debug`. Seeded days (`scripts/seed_owner_day.py`) log the office's calls the same way.
- **Open points.** The agent's callback opening line does not yet ask the person's name, so until it does a callback is confirmed by the read-back or a name check the agent makes unprompted (the line is high-touch wording and changes through `/round`). There is no tool that closes a flag with a resolution, so "a flag closed with a resolution" is not one of the ways a reason counts as handled. `sid` is assumed to be the id our telephony gives the call; a sandbox call settles that and what the real PUT needs.

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

The mock ServiceTitan serves the Telecom call feed with a direction field (`servicetitan_mock/routes/settings.py`), and the adapter reads it (`FsmAdapter.calls_to`).

## Recommendation

B, with A as the fallback if Summit Air's office doesn't place calls through ServiceTitan. B keeps the page read-only and counts calls that were really made, which is what the owner is asking when they ask whether callbacks happen.

## Research notes

- ServiceTitan's Telecom API lists calls with a direction (Inbound or Outbound) and the customer they were matched to; our mock models the same shape (SOURCE: `servicetitan_mock/routes/settings.py`, `shape_call`).
- `scripts/seed_owner_day.py` used to mark the callbacks the office returned as done directly in the database. It now logs the office's calls tagged resolved and lets the job read them.
