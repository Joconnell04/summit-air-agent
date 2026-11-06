# Presage

A local call copilot Jackson built for Zoom and Teams: it listens, transcribes, and surfaces a sourced answer card within a second or two. Lessons only; no code is copied.

- **Speculative prefetch.** Draft likely answers while the caller is still talking, then serve the card the moment the question is final. Late speculative drafts were most of the "early" cards and had to be held back. Summit Air: start the model request on the final transcript (preemptive generation) and prefetch the day's open slots and membership lookup once the ZIP or phone number is heard.
- **A fast local gate before model calls.** A small local classifier fronted every slower decider and ran in tens of milliseconds. Summit Air: a keyword-and-rule urgency check (gas, CO, smoke, no heat, elderly or medical) runs before the model and cannot be overridden by it.
- **Measure end to end, then check the harness.** A harness that waited for machine load to drop never started, and a timing bug in an audio test inflated STT by seconds. Summit Air: one latency script with per-stage numbers, timeouts on every replay, and a sanity check of the clock before tuning.
- **Transcription errors on numbers.** Spoken numbers ("twelve percent", "Q3 parsed as 3") and digits split across turns were a recurring error class. Summit Air: number-word normalisation, phone and ZIP validation, and a read-back in groups before booking.
- **Billing- and latency-aware routing.** Speculative drafts ran on the cheap path; live answers ran on the faster paid path; a fallback provider caught failures. Summit Air: small fast model on live turns, a fallback model on error, and no large model in the live path.
- **Resident models compete.** A second large model in memory stalled the transcriber until the gate was shrunk. Summit Air: keep the live path to one speech stack and one conversation model.
- **Honest scoring.** A hit counted only if the right figure was on the earliest answering card within a time window. Summit Air: evals count a booking correct only if the slot, address and phone in the database match what the caller said.
