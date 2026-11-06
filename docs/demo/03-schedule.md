# Demo 3: the schedule and double-booking

Video: [videos/03-schedule.mp4](videos/03-schedule.mp4), about 1 min 55 s. Data: the seeded demo day, with the mock ServiceTitan served beside it (docs/demo/source/serve.sh).

**What this proves.** The server re-checks who is free at the moment of booking, refuses a window nobody can take, and writes every job to ServiceTitan during the call.

## 1. "Prove it didn't double-book that slot."

- **Click.** Schedule. Point at one arrival-window column: one job per technician per window.
- **Show the code.** `app/booking.py` lines 131 to 161: capacity is looked up again right before `create_job`; if the technician was just taken, another is tried; only a window with nobody left is `slot_taken`. Then `servicetitan_mock/schema.sql` lines 196 to 200, the unique index.
- **Run, if asked.** The duplicate insert, on a copy (from docs/demo/source/capture_text.sh), which fails with `UNIQUE constraint failed`. And `uv run pytest -v tests/tool_server/test_ts_booking.py -k "racing or parallel"`.
- **What it proves.** Eight callers racing for the last opening get it once (`test_eight_callers_racing_for_the_last_slot_get_it_once`); eight retries of one booking book once.
- **Talk track.** "Between offering you a time and booking it, someone else might take it. So the server checks again at the moment it books. If that window just filled, it tells the caller and offers the next one. We test that with eight callers going for the last slot at once: one gets it."
- **Be precise.** The unique index is in our mock. Real ServiceTitan has no such lock (its capacity is advisory), so the server's re-check is the guard in production; the mock's index is a second net for the tests.

## 2. "Is it really in ServiceTitan?"

- **Click.** Today, Jobs booked: "all 10 sent" beside the ServiceTitan logo.
- **Run, if asked.** The job 608 query: technician, today, 0800-1000, Scheduled.
- **What it proves.** The booking is a ServiceTitan job with the call's notes as its summary (priority and why, who's home, what was tried).
- **Talk track.** "It's not a list on our side you'd have to copy over. Each booking is a job in ServiceTitan, with the notes your tech needs, written while the caller is on the phone."

## 3. "Can it get the newborn family in sooner?"

- **Click.** Today, Jobs booked, the 11:15 AM row ("Moved earlier"). Then Calls, the 2:20 PM call placed by us.
- **What it proves.** The server watches for an earlier opening for urgent jobs booked out and offers it on a call back; the job moves in ServiceTitan on a yes.
- **Talk track.** "This family has a newborn and the first opening was tomorrow. When a slot opened this afternoon, the system called them back and moved them up."
- **Be straight.** On the Retell line, outbound calls aren't built yet (voice/retell/README.md, "Outbound callbacks: Not built"). The seeded day shows what the server does; on today's line the office would make that call. Say it as a rollout item.
