# Eval round 1: five real failures

Round 1 of the eval co-authoring plan (docs/explorations/008-eval-set-quality.md, section 7). Read each one, say what's wrong in a line or two, then answer the question at the end. About 15 minutes.

Run `20261103-064002-livekit-fast-standard`: our LiveKit stack, profile `fast` (Haiku 5.5), the real prompts, 30 standard scenarios. 69% pass (20 of 29 scored, 1 error), cost $0.64 (MEASURED from token usage). Reply delay: plain p50 597 ms, lookup p50 1214 ms (MEASURED, text only, no audio). Full transcripts: `evals/results/20261103-064002-livekit-fast-standard.json` and the LangSmith experiment of the same name.

## 1. A homeowner is turned away as a business (`tune-up-known-caller`)

Dennis owns and lives in a two-family house and wants his yearly boiler tune-up. He mentions a tenant upstairs.

> Agent: I'm sorry, but the caller describes this as a business or a building you manage, and Summit Air only works on homes.
> Dennis: This is a house I own and live in... I've used Summit Air before for this same boiler.
> Agent: Since the building has a tenant upstairs, our system treats it as a building you manage.

Failed: no booking. My read: the residence check treats any tenant as a managed building, and the refusal leaks internal wording ("the caller describes this as").

## 2. A trash smell sends the caller to 911 (`vague-smell-cleared`)

Marcia says the basement smells "kind of off like eggs maybe", then says it's definitely not gas, the CO alarm is quiet, and it's an old trash bag.

> Agent: If a window is right there, open it. Then get everyone out of the house now and call 911 once you're outside.

Failed: the gas lines fired after the caller cleared the check question, and no visit was booked. My read: the server's safety trigger fires on "eggs" before the clearing answer counts. The open question is how sure a caller has to sound before we stand down.

## 3. A reschedule becomes a second booking (`reschedule-own-booking`)

Maria asks to move today's visit to Friday morning. The agent agrees, then calls `book_appointment`, not `reschedule_appointment`. She now has two visits. It also calls her a renter after she said nothing of the kind.

> Agent: I'm booking now. You're all set for Friday the 6th, with the technician arriving between 8 and 12.

Failed: reschedule never ran, a new booking was made. My read: a server gap (a caller with an open job can be booked again without a check) plus the agent picking the wrong tool. Today's visit is in a window that has already started, which is issue #48, being fixed now.

## 4. "After 5" becomes "by 5" (`complaint-flag`, and three more calls)

Thomas, unhappy about a bill, asks for a call after 5 PM.

> Agent: I've let our team know, and someone will call you by 5 PM.

Failed: the grader caught a promised time the caller didn't ask for. The same thing happened on three other calls ("after four", "after 1", "around three or four"). My read: the flag tool only takes "call back within N minutes", so "after 5" can't be said, and the server's line always says "by".

## 5. A renter with no known approver is booked anyway (`renter-landlord-unknown`)

Marcus just moved in, doesn't know who owns the building and can't reach the super.

> Agent: You're all set for today, Monday, with the technician arriving between 12 and 4... Since the approver isn't known yet, I've also asked a person to follow up.

Failed: expected a flag or callback, not a booking. The read-back of his number also went round three times. My read: the booking gate doesn't require an approver for a renter.

## Also failed (not shown)

- `co-alarm-clear`: told an evacuating caller "I'll get a technician out to you", asked him for a callback time, and said "get everyone outside" after he said he was alone.
- `after-hours-no-heat-cold`: 48 F inside after hours, booked for the morning with no on-call flag.
- `cancel-offers-windows`: the cancel was refused as "already dispatched" and the caller was handed to a person.
- `gas-furnace-no-trigger`: long, contradictory phone-number turns that leaked internal process.
- `baby-no-heat-standard`: didn't finish in 16 caller turns (harness error).

## Your turn

1. For each of the five: what's wrong, in a line or two. Say "agree" if my read is right.
2. Which failure would embarrass you most in front of Revin?

## Jackson's notes (verbatim, transcribed)

> Okay, I'm reading through round one failures. Yeah, all of these are definitely failures, and they all need fixes that don't overcorrect the issues at hand. And definitely, I'm just looking at the transcript where it's like, I'm sorry, but the caller describes this as a business or a building you manage. If the agent was saying this to the caller, it shouldn't say, but the caller describes dot, dot, dot. Yeah, don't over-sensitize yourself for the 911 stuff. That should be pretty rare. And yeah, implement better logic for the reschedule booking thing. Yeah, and definitely fix the timing thing. Maybe we can like stage gate that with like a verification system. I don't know. Implement and surface to me the fix you use for that briefly. Number five isn't that bad, though. That's to me, that doesn't sound like crazy to have somebody come to service your equipment without actually being the approver. As long as a follow-up call is properly scheduled. Anyway, review what I said.

## What the notes mean for the suite

| # | Verdict | Fix |
|---|---|---|
| 1 | Failure, agreed | Refusal never repeats internal wording; an owner who lives in a two-family house with a tenant is a home (spec already says so). |
| 2 | Failure, agreed | A clear "not gas" answer to the check question stands the gas lines down. Real gas wording after that still fires. Keep it narrow: don't make the trigger less sensitive in general. |
| 3 | Failure, agreed | The server refuses a new booking for a caller who already has an open job at that address and points the agent to reschedule. |
| 4 | Failure, agreed | Callback times are checked by the server against what the caller asked for (see the fix note in the PR). |
| 5 | Not a failure | Booking a renter whose approver is unknown is fine as long as a follow-up call to find the approver is scheduled. The scenario's expectation changes to that; the repeated phone read-back is still a defect. |

Failure types so far: wording that leaks internal process (1, and `gas-furnace-no-trigger`); safety rules that overfire (2, `co-alarm-clear`); the server trusting the agent's tool choice (3); promises that don't match what the caller asked (4).
