# Summit Air Jackson: changes

Newest last. Each line is one snapshot, made after a change was published in Retell.

- 2026-11-05 20:41 UTC: agent v2, LLM v2. Baseline: the agent as Jackson built it with Retell's builder assistant (version 2), before any changes from the coordinator.
- 2026-11-05 20:49 UTC: agent v2, LLM v2. Coordinator change set 1: begin_message {{greeting}} with web-call defaults and {{call_context}} at the end of the prompt; safety lines left to the server with a say-exactly rule; one sensitivity question on no-heat and no-AC calls only; windows as half-hour blocks (window_minutes, a named time via arrive_after, day takes today and weekdays); built-in hang_up removed in favor of our end_call; the HVAC basics knowledge base (filter 0.8, top 2, technical questions only); accurate speech-to-text; contact memory off for testing.
