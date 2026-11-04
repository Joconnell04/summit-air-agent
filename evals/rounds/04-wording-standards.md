# Round 4: refining the wording standards

Round 3's standards made the grader stricter, and the standard suite settled at 80% over two runs (20261104-031113 and 20261104-033200, MEASURED from the result files). These are the 15 standards behind those failures, the five that cost the most first. For each, say keep, loosen or change in a line. One question underneath them all: a standard broken twice on a call fails it today; should some of these only lower the score?

## Most trouble

1. **Read back once.** The address and number are read back once, in one sentence; after a correction, the corrected sentence is said once. Broke in 6 of 18 failures: an early "so that's 1857 77th Street, correct?" and then the real read-back, or re-reading after the caller already confirmed.
2. **Never re-ask what's answered.** A ZIP given in the first sentence, a name, "no pets", who approves. Broke in 6 of 18. Does re-confirming a detail that may have been misheard count, or only re-asking from scratch?
3. **One question at a time.** Two questions stacked in one turn ("How long has it been off, and how cold is it?"). Named in most grader notes. Is a closely paired question (address and name together) acceptable?
4. **No repeated acknowledgment.** "Got it" said three times in a call. Named in most notes. Fail, or only a lower score?
5. **"Pet", not "dog".** Broke in 2, including the agent echoing the caller's own "no dog". Is echoing the caller's word fine?

## Others the grader checks

6. **One goodbye.** After the caller says goodbye, nothing more is asked and the call ends; on a safety call the sign-off is the only ending.
7. **"Top priority" only on Urgent calls.** The agent said it on a call the server hadn't marked Urgent, and skipped it on one that was.
8. **A gas worry with no smell gets no safety lines.** "Could it be a gas thing?" with no smell, leak, alarm or symptom is reassured, not sent outside. Broke in 1.
9. **Spell a street only when it's unclear.** Asking to spell a clear, found street name counts as re-asking.
10. **Callbacks carry urgency and a time.** "As soon as they can", and "around 5:30" when the caller said "after 5".
11. **Access notes become actions.** "I'll let the technician know to call when they're downstairs", not just "Anything else?".
12. **Soft optional questions.** "No worries if not", "if you know" on technical or optional questions.
13. **Confirmation names the day, the date and the window.** "Thursday, November 5, between 12 and 4."
14. **No inside words.** Not "read-back", "flag", "server", a tool name, or "comfort advisor".
15. **Recommend, don't command, on safety calls.** Say it once, offer once more at most, then help; never "stay on the line", never name a relative.

## Your turn

For each of the 15, keep, loosen or change. Then: which, if any, should only lower the score rather than fail a call when broken twice?

## Jackson's notes (verbatim, transcribed)

> Okay, so for round four. Investigate a better way we could handle the reading back thing, given that it's causing so many issues, and that's such an obvious mistake. Same thing for never re-asking what's answered, because that's fundamentally the same behavior of redundancy. Yeah, do one question at a time. Question, wait, answer, accept. Like, only when it's two separate questions. And you can ask an or question in one sentence, just not like two individual direct questions, like how long has it been and how cold is it? Repeated acknowledgement is not inherently bad. So maybe we need to change the greater on that part. Obviously some variability is nice, but sometimes it's good to use the same acknowledgement. It makes it very clear. Also, figure out a fix to the pet and dog thing. I'm very confused at how that is causing so many problems. Investigate whether it's the grader's fault or the voice agent's fault for point number six on the goodbye thing. Yeah, and figure out the same stuff for top priority, only on urgent calls. It shouldn't be saying that and not doing it. These should be easy things to implement. Also, surface to me how you're going to go about implementing them without hurting latency too bad. Same thing with the gas worry. And the re-asking, although don't worry too much about that one. Unless it caused an issue. And saying around 5:30, if the caller said after 5, that's good, that's after 5. Also, make sure there is an actual system that's operating to properly check availability and book appointments and stuff. And test concurrency too, for like two calls at once trying to book the same appointment, and if one is handled versus not. Make like one or two evals for that.

## What the notes mean

| # | Standard | Verdict | Change |
|---|---|---|---|
| 1 | Read back once | Keep, fix the system | The server speaks the read-back itself and owns when it's said; a model restatement of a confirmed address is dropped. |
| 2 | Never re-ask | Keep, fix the system | The server tells the model each turn what's already known, so nothing answered is asked again. |
| 3 | One question at a time | Keep, clarified | Two separate direct questions in one turn break it; one "or" question doesn't. |
| 4 | Repeated acknowledgment | Drop as a fail | Repeating "Got it" is fine and can be clearer; some variety is nice but isn't graded. |
| 5 | "Pet", not "dog" | Keep, find the cause | Investigate whether the agent or the grader is at fault. |
| 6 | One goodbye | Keep, find the cause | Investigate whether the agent or the grader is at fault. |
| 7 | "Top priority" only on Urgent | Keep, fix the system | Only the server says it, and only when it marked the call Urgent. |
| 8 | Gas worry, no smell | Keep, fix the system | No evacuation advice unless the server heard a safety signal. |
| 9 | Spell only when unclear | Keep | Fix only if it keeps causing failures. |
| 10 | "Around 5:30" for "after 5" | Keep | Already right. |
| 11 to 15 | | Keep | |
| New | Booking under load | Add | Confirm availability and booking run on the real system, and add one or two evals with two calls booking the same slot at once. |
