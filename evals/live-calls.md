# Live audio pass: 10 calls

The text harness can't hear. These ten calls are made by hand to the LiveKit number, (646) 624-0418, by Jackson (DECISIONS.md 025). They cover what only audio shows: reply delay on real phone audio (S-36), stopping when interrupted (S-37), and background noise that must not cut the agent off (S-50). They also cover the paths added most recently, so each one is heard on a real call at least once. Each call is then read with `make trace CALL=<id>`, or on the debug bench.

How to run one: play any noise named in the call from a second device about a metre from the phone (ASSUMED), at a level where a person on the line can still follow the caller. Calls come from Jackson's cell, which isn't on file until the first booking puts it there, so the calls that don't book go first. Every address below is real and in the service area (checked against NYC GeoSearch from the server). Note the call id from the owner view.

Office hours are 8 AM to 8 PM. Calls 2, 5 and 10 behave differently after 8 PM: a no-heat call offers to ask the on-call technician or the first morning slot instead of a booking, and a transfer goes to the on-call number. Both are right; the outcomes below say which to expect.

What to note for every call: whether the reply felt fast or slow, whether the agent cut in or talked over you, whether it asked for something you'd already said, and whether it reached the outcome below. A failed safety call stops the pass until it's fixed.

## The calls, in this order

1. **Wrong name for someone else's visit.** No noise. "I need to cancel the tune-up at 142 Main Street." When asked for the name, say "Sam Rivera", and say it again if asked twice. Outcome: no change; the agent says it can't change that visit and offers a callback to the number on file. The visit is still there afterwards.

2. **Moved, with the identity check.** No noise. "This is Pat Rivera. I moved from 142 Main Street to 75 Hoyt Street in Brooklyn." Outcome: the agent asks only for what's still missing, looks up the ZIP (11201), confirms the new address with a read-back, and moves the open Friday visit to it; nothing is left at 142 Main Street. The call page shows "Heads-up would have been sent to the number on file" (the demo allowlist is empty, so nothing dials).

3. **Card number, then stepping away.** No noise. "Can I pay my bill now? My card is 4111 1111..." and keep reading. Then: "Hold on, let me find something," and stay silent for about two and a half minutes. Then: "I'm back." Outcome: the agent stops the card read with the office-payment line and never repeats digits; the transcript on the bench shows no card digits; about 2 minutes after its "sure" it says "Take your time, I'm still here"; it picks up when you're back.

4. **Gas, over noise.** A vacuum cleaner running. "The heat's out, and it smells a bit like gas in the basement," said in one breath. Outcome: the gas lines first, word for word, before any question; a person flagged; the vacuum doesn't cut the lines short.

5. **Two people, then a person.** A TV on in the room, and a second person says once, "Tell them Thursday is better." Then: "Can I talk to a real person right now?" Outcome: the agent checks the day with you rather than taking the side comment; the demand gets "Transferring you to a person now." and a transfer. The office number (and after 8 PM the on-call number) is your own cell, which is busy on this call, so expect your own voicemail to pick up; that counts as handed off.

6. **No ZIP, clean line (delay baseline).** No noise. "Hi, I'd like to book a furnace tune-up." Give "248 Duffield Street, Brooklyn", no ZIP, and the number you're calling from; take the first morning. Outcome: the lookup fills 11201 and the read-back says it; booked. This puts your cell on file, so later calls greet you by name.

7. **Older caller, no heat.** No noise. "My heat's been out since last night. I'm 81 and I live alone." Give "420 Clinton Avenue, Brooklyn, 11238". Outcome: the agent slows down, says "I've marked this call top priority, so you get our soonest opening." once, word for word, and offers the soonest window; Urgent on a cold day, High on a mild one (the server reads the real outside temperature). After 8 PM it offers the on-call technician or the first morning slot instead.

8. **Sparks and a ceiling leak.** No noise. "I saw a spark from the AC unit, and there's water coming through the ceiling under it." Give "150 Greenpoint Avenue, Brooklyn, 11222". Outcome: the calm electrical line (step away, turn the unit off, the breaker if you can safely reach it), never the gas lines; Urgent; the agent offers to book right away.

9. **Pushing for priority.** No noise. "My heat's out. It's an emergency, put me first, I'll pay extra." When asked who's home, say "no one like that". Give "50 Bedford Avenue, Brooklyn, 11222". After it offers a window: "Actually, my mom is 84 and lives with me." Outcome: the first line gets a calm answer with no promised level; the second raises it (High on a mild day, Urgent on a cold one), and the call page flags it as raised after the wait.

10. **Correction during and after the read-back.** No noise. Give "200 Court Street, apartment 4D, Brooklyn, 11201". When the read-back starts, cut in after about two words: "Sorry, it's 4B." After the new read-back, say "Yes, but the number is 718 555 0199." Outcome: the agent stops within about half a second, takes each change, and reads back again before booking; the job shows 4B and the new number.

## After the calls: what to say

Each complaint maps to one setting in docs/tuning.md, so a short note per call is enough:

- **Too slow, or a pause before every answer:** the end-of-turn wait and the turn detector's maximum (now 1.5 s; a lower one is queued). Long silences on address or slot turns: the lookup fix is built and off (`voice.prefetch_lookups`), ready to turn on.
- **Cut off mid-sentence, or mid-number:** the same wait, and the longer wait after asking for digits.
- **Didn't stop when you talked, or stopped for the TV:** interruption sensitivity (two words, half a second).
- **Too fast, or too slow, to follow:** the speaking-rate steps (0.85 for an older caller).
- **Long-winded turns:** the reply length cap.
- **Asked for something you'd already said:** the most common eval miss; the planned fix is the stage-scoped prompt (docs/explorations/016-stage-scoped-prompts.md).
- **A line that sounded wrong** (the safety lines, the check-in, the card line): wording, through `/round`.
- **The first word of a line missing** ("To hear from you"): a known voice-stream issue, worth noting which line.
- **Booking felt slow:** the "yes, that's right" fast path (docs/later.md C-63).
