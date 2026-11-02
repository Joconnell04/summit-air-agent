# Users

Who the agent serves, what each of them needs from a call, and what that means for the build. Callers come first. The people at Summit Air who act on what the agent produces come second.

What the kickoff tells us (SOURCE: docs/assignment.md): residential only, 40 technicians, three counties in NYC, phones overwhelmed on the first heat wave or cold snap, and most calls are an AC that went out, a furnace that won't start, or annual maintenance.

## Callers

### 1. No heat or no cooling

The most common call. A homeowner whose system stopped, usually stressed, often calling during a surge when the wait to reach a person is longest.

- Wants: someone out today or tomorrow, a real arrival window, and to know it's handled.
- Worries: hold music, being upsold, a window that slips.
- Good call: tells the story once, hears the window and what happens next, gets a text confirming it.
- For the build: let the caller talk first and fill gaps after. Offer the earliest real slot. Ask the urgency questions in the flow of the call (who's in the house, how cold or hot is it inside).

### 2. Vulnerable household

The same broken system, plus someone at risk: an older person, a baby, a medical condition made worse by heat or cold. The caller is often not that person. An adult child calling for a parent in another borough is typical.

- Wants: to be taken seriously at once.
- Worries: being handled like a routine booking.
- For the build: ask about vulnerable people on every no-heat or no-cooling call, because callers don't volunteer it. A priority flag with the reason. The person on site is the tech's contact; the caller may be the one authorizing the visit.

### 3. Safety caller

Gas smell, a carbon monoxide alarm, a burning smell or smoke. Often mentioned in passing ("it smells a bit like gas, but anyway the heat's out").

- Needs: the safety instruction before any other question.
- For the build: the check runs on every turn, not only at the start. Fixed wording. The server records that it was said, holds booking questions until then, and flags a person.

### 4. Maintenance scheduler

Calm. An annual tune-up, often a member whose plan includes it, sometimes prompted by a reminder.

- Wants: a convenient time, quickly, without repeating details Summit Air already has.
- For the build: look up the caller by phone first and use what's on file. Offer windows that suit them. In a surge these jobs yield to urgent ones.

### 5. Member

Any of the callers above, on a maintenance plan. Members expect to be recognized and to get the plan's perks. Some don't know they're members; some plans have lapsed.

- For the build: look them up before asking anything Summit Air already knows. Name the benefit back. Never promise a perk the plan doesn't include.

### 6. Renter, or someone who can't authorize the work

A tenant, a house-sitter, a relative staying over. In NYC a tenant in heat season is owed heat by the landlord (C-13, rules to be checked).

- Wants: the system working again; may not be able to approve a paid visit.
- For the build: ask who owns the home or who is paying only when it matters. Record the authorizer separately from the caller. The route for a tenant with no heat is settled in call journeys.

### 7. Repeat caller

Has an open job or a recent visit: "where's my tech?", "the same problem came back", reschedule, cancel.

- For the build: find the open job first, never create a duplicate, and give status from the FSM rather than guessing.

### 8. Commercial caller

A property manager, a restaurant, an office, a super for a large building. Summit Air doesn't serve them.

- Wants: to know quickly whether Summit Air can help.
- For the build: spot it early from the signals in C-05, decline politely with a message to the office, never book. The grey zone (an owner-occupied two-family house, a small landlord, a co-op unit) is drawn in call journeys.

### 9. Not a customer call

Wrong numbers, robocalls, vendors, job seekers, and people testing the bot with off-topic or adversarial asks.

- For the build: end politely and quickly, log the call type, stay in role.

### Traits that apply to any caller

- Upset or rude: stay calm, don't argue, offer a person.
- Hard of hearing, elderly, or slow to answer: slower pace, shorter questions, read-backs.
- Prefers Spanish or another language: switch or hand off (C-16).
- Two people on the line, or a caller relaying answers from someone else in the room.
- Asks if it's a robot: says it's an AI assistant for Summit Air.

## Summit Air staff

### Owner

The person Paul plays in the demo.

- Wants: more calls answered and booked during a surge, no missed emergency, the brand sounding good, and technicians sent only on jobs they can do and bill.
- Worries: the agent books a commercial job, misses a gas smell, quotes a price, promises a slot that doesn't exist, or sounds like a robot. Also cost, liability, and what happens when it breaks.
- Sees: today's bookings, priority flags with reasons, calls that need a person, and evidence it works (the eval scoreboard).
- For the build: every requirement shows up both in a live call and in the owner view.

### Dispatcher or office staff

Answers the phones today and can't keep up in a surge. With the agent, works the flags and the schedule instead.

- Wants: clean bookings in the FSM, priority with a reason, handoffs that carry context so the caller isn't asked again, and no double bookings.
- For the build: records written in the FSM's shape. Each flag carries its reason and a link to the call. Warm transfer during business hours.

### Technician

Reads the job before arriving.

- Needs: system type, brand and rough age, symptoms and when they started, thermostat reading, what's been tried, access, pets, and who to ask for on site (C-07).
- For the build: structured job notes, not a transcript. Separate fields for the on-site contact and the authorizer.

### On-call person after hours

Gets urgent flags at night and on weekends (ASSUMED: Summit Air has an on-call rotation).

- Needs: a short text saying what, where, who, and why it's urgent.

## When needs conflict

1. A caller in danger: gas, carbon monoxide, or a vulnerable person without heat or cooling.
2. A caller with a broken system.
3. The owner's business: booked, billable, the right kind of job.
4. The dispatcher's and technician's time.

For example, in a surge a routine tune-up gets a later slot so an urgent call gets today's.
