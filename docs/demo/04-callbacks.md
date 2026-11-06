# Demo 4: callbacks and leads

Video: [videos/04-callbacks.mp4](videos/04-callbacks.mp4), about 1 min 36 s. Data: the seeded demo day, with the mock ServiceTitan's call log.

**What this proves.** Every promised callback has a time the server gave, shows up on Today, goes red when late, and is marked done only when ServiceTitan's call log shows a resolved call. Calls it couldn't book become leads or are turned away with a reason.

## 1. "Where do I see the callbacks it promised?"

- **Click.** Today, the Callbacks tab on the right. Point at the two marked Late, then at the Late callback rows in Needs attention. Open the 10:20 AM complaint call.
- **What it proves.** Callbacks are stored with a due time by the server (spine rule 8: the model can't make one up), and late ones raise themselves to Needs attention.
- **Talk track.** "Every time it says 'someone will call you back', it lands here with the time it promised. These two are late, so they've moved up next to the safety calls. Nobody has to remember them."

## 2. "How do I know the office actually called back?"

- **Show the code.** `app/jobs.py` lines 227 to 234: done only when ServiceTitan's call log shows an outbound call to that number, tagged Callback resolved.
- **Run, if asked.** The call log query (docs/demo/source/captures/sql-call-log.txt): `office-c09` to (347) 555-0188 at 18:30 UTC (1:30 PM), Callback resolved. Then `uv run pytest -v tests/tool_server/test_ts_callback_done.py -k voicemail`.
- **What it proves.** Your office works in ServiceTitan as it does today; the agent reads the result. A voicemail or no answer never closes a callback.
- **Talk track.** "We don't take anyone's word for it, ours included. When your office calls back from ServiceTitan and tags it resolved, it turns green here. A voicemail doesn't count."

## 3. "What about the calls it couldn't book?"

- **Click.** Today, the Leads tab, then Turned away.
- **What it proves.** A heat pump quote, a membership question and an office building each become a lead sent to sales in ServiceTitan. Out-of-area (Hoboken) and commercial calls are turned away politely, with the reason recorded.
- **Talk track.** "Not every call is a booking. A replacement quote or a membership question goes to sales as a lead. Commercial and out-of-area calls get a polite no, and you can see each one and why."

## If Paul pushes

- "Who gets the callback?" Each callback has an owner (office or on-call). After hours, urgent ones email the on-call person (Settings, On-call alerts).
- In the seeded day, two out-of-area examples use ZIP 10011, which is now in the area (all five boroughs, DECISIONS.md 105), so they show as "in the service area". Use the Hoboken call (12:10 PM) as the example.
