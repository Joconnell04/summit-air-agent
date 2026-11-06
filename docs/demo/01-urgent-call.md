# Demo 1: an urgent call, start to finish

Video: [videos/01-urgent-call.mp4](videos/01-urgent-call.mp4), about 1 min 54 s. Data: the seeded demo day (`make owner-demo`), not the live server.

**What this proves.** The agent gathers facts in a normal conversation; the server sets the priority from the company's own rules, says why in plain words, and can only raise it.

## 1. "Show me what happened on that 7:42 call."

- **Click.** Today. Point at Need a person, then scroll to Jobs booked and open the 7:42 AM row (No heat, Urgent, 8 to 10).
- **Point at.** The caller's two lines (the parent is 84, 52 degrees inside), the read-back and the caller's yes, then "Flagged for a person".
- **What it proves.** Booking waits for a confirmed read-back (a server gate, not a prompt line), and an urgent call before office hours alerts the on-call team with a timed callback.
- **Talk track.** "This came in at 7:42, before your office opened. The caller's parent is 84 and it's 52 degrees inside. It read the address and number back before booking, got a yes, put a tech there 8 to 10, and told your on-call person to call back within 20 minutes."

## 2. "Why is it Urgent? Did the AI decide that?"

- **Click.** On the same call, point at Why in the Job booked panel. Then click Technical details and find `"priority": null` in the `find_slots` block.
- **Show the rule.** `tenants/summit-air/agent.yaml` lines 168 to 185: below 55 F inside is Urgent; anyone 60 or older counts as at risk.
- **Run, if asked.** `uv run pytest -v tests/tool_server/test_ts_priority_caller_words.py`. The one to name: `test_asking_for_urgent_with_no_fact_raises_nothing`.
- **What it proves.** The server sets the priority from the caller's own words and the rules; a level the model asks for is only logged (DECISIONS.md 072, 073). The reason quotes the caller, so a dispatcher can check it.
- **Talk track.** "The AI didn't pick Urgent. It passed along what the caller said, and your rules did the rest: under 55 inside with an older person at home. Those cutoffs are yours to set. And a caller who just says 'make it urgent' with no new fact doesn't get bumped."

## 3. "What if they mention grandpa only after hearing the wait?"

- **Click.** Calls, the 9:03 AM call. Point at the caller's late line ("my dad lives with us, he's 79"), then at Raised: "After the caller heard the wait", and the same note in the header.
- **What it proves.** A fact given late still raises the priority, and the raise is flagged for review (`raised_after_wait`, DECISIONS.md 072).
- **Talk track.** "We believe the caller, because missing someone at risk is worse than a false alarm. But we mark it, so your dispatcher sees this one came up after they heard the time, and can call if it looks off."

## If Paul pushes

- "Can it get it wrong?" The facts come from speech recognition, so a misheard number is possible; the read-back catches address and phone, and the dispatcher sees the quoted line behind every Urgent.
- Note when demoing: in the seeded day the Schedule shows a different customer name on job 608 than the call page (see docs/later.md). Open jobs from the call page, not by name on the Schedule.
