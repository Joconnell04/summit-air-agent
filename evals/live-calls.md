# Live audio pass: 10 scripted calls

The text harness can't hear. These ten calls are placed by phone to each stack, the LiveKit number (646) 624-0418 and the Retell number (678) 336-6730, by an automated caller that speaks the script or by hand from it (decision 06). They cover what only audio shows: reply delay on real phone audio (S-36), stopping when interrupted (S-37), the bridge line (S-37a), and background noise that must not cut the agent off (S-50). Each call is then read with `make trace CALL=<id>`.

How to run one: play the noise track named in the call from a second device about a metre from the phone (ASSUMED), at a level where a person on the line can still follow the caller. Speak the lines in order, waiting for the agent unless the line says to cut in. Note the call id from the owner view or the trace.

What to record for every call: median and slowest reply delay from the trace's turn timings, whether the agent stopped within about half a second of being cut off (ASSUMED bar), any false stop caused by noise, and whether the call reached the outcome below. A stack that fails a safety call is out of the bake-off (docs/bakeoffs.md).

## The calls

1. **Quiet tune-up (baseline).** No noise. "Hi, I'd like to book a furnace tune-up." Give a Brooklyn address, the number calling, take the first morning. Outcome: booked. Use for the clean delay baseline.

2. **Street noise, no heat.** Traffic and a bus pulling away. "The heat's out in my apartment since last night." Give a Queens address and the number calling. Outcome: booked, priority from what was said. Check: no false stop from the bus.

3. **Kitchen noise, ZIP in digits.** Running tap and dishes. Give the ZIP slowly as single digits, "one, one, two, three, one", pausing between digits. Check: the agent waits for the whole ZIP rather than answering after two digits (longer end-of-turn wait after asking for digits, docs/latency.md).

4. **Interrupt the read-back.** No noise. When the agent starts reading the address back, cut in after about two words: "Sorry, it's unit 4B, not 4D." Check: the agent stops within about half a second (ASSUMED), takes the correction and reads back again. Outcome: booked at the corrected unit.

5. **Interrupt with a cough and a TV.** A TV talk show in the room. While the agent offers times, cough twice and say nothing. Check: the cough and TV don't stop the agent (an interruption needs recognized words, S-50); if it does stop, it resumes the reply.

6. **Gas mentioned over noise.** A vacuum cleaner. "The heat's out, and, um, it smells a bit like gas in the basement." Outcome: the safety lines first, word for word, no booking questions after them, a person flagged. The vacuum must not cut the safety lines short.

7. **Carbon monoxide alarm beeping.** Play a CO alarm chirp in the background. "My carbon monoxide alarm keeps going off." Outcome: the CO lines first; the agent signs off rather than hanging up.

8. **Slow lookup, bridge line.** Ask "When were you last out here?" as a known caller (call from a number on file), which needs a history lookup. Check: if the lookup takes over about 0.7 seconds (the bridge threshold in docs/latency.md), one short bridge line plays, it never overlaps the agent, and it isn't repeated later in the call.

9. **Two people talking.** A second person in the room speaks over the caller once: "Tell them Thursday is better." Check: the agent confirms who is home and which day, and doesn't treat the side comment as the caller's final answer without checking.

10. **Hard of hearing, long pauses.** No noise. Answer each question after a pause of about four seconds (ASSUMED, just under the 5-second check-in in docs/call-journeys.md) and ask "Sorry, what?" once. Check: the agent waits, repeats more slowly and briefly, and doesn't fill the pause with a stream of prompts. Outcome: booked.
