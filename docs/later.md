# Come back to later

Things Jackson set aside on purpose. Nothing here is dropped; each item gets walked before V1 is called done (phases.md, phase 3). Add an item whenever Jackson says "later", "come back to that" or "not now", with where it came from.

| Item | Why it waits | Where it came from | Status |
|---|---|---|---|
| Spanish and other languages: live Spanish, callback for others, Deepgram Flux Multilingual, Spanish lines in every bake-off | A nice extra, not a deciding factor for V1 | #5 discussion; C-16 | open |
| Side model in at least one place (Jev, post-trained Laya or OpenAI Decisions), where a bottleneck shows up | Launch with none and a slot; add one only where it fixes a measured bottleneck | #5 discussion; C-35; evals/decision-models/PLAN.md | open |
| Model matrix bake-off (Haiku, Sonnet, Opus, OpenAI, Gemini) | Designed once the agent works end to end | #5 discussion; docs/bakeoffs.md | open |
| Playbooks per call (surge, member, after hours) | Unverified idea; revisit after evals show where calls go wrong | users r2 and r3; C-32 | open |
| Warm transfer to a person | No real staff to transfer to in the demo | C-11 | open |
| Production capacity: 5 then 15 calls at once | Demo runs about 3; the step-by-step plan is docs/scaling.md | #4, #16, DECISIONS.md 008 | open |
| Booking confirmation texts | Not asked for by the assignment; business texting needs carrier registration (1 to 3 weeks); terms and privacy pages already live | DECISIONS.md 007, #15 | open |
| Reply delay targets (spec S-36) | Accepted for now; revisit whenever Jackson gives feedback on delays or pauses | spec r2 | open |
| Replace ASSUMED latency values with measured ones | Needs the first real calls | latency.md, #7 | open |
| Set the eval pass-rate floor: run the standard suite on the real prompts, adopt it, floor at its rate rounded down to 5% | Waits for the server and prompt fixes from the build review to land | #27, DECISIONS.md 011 | open |
| Call recordings on the call page (story O-6) | None are stored today; DECISIONS.md 014 named this the first cut. The call page says the recording isn't kept | brief 07, #53 | open |
| Editable numbers on the owner view: hours, urgent hold, outbound hours, tighten-only brackets (exploration 009 option B in full) | V1 edits wording and guidance only; numbers wait until Summit Air asks to change them | Exploration 009, questions 4 and 5; #56 | open |
| "If the caller says X then Y" rules from a fixed menu (exploration 009 option D) | A rule engine is 8 to 12 days (ASSUMED) and waits for three real asks | Exploration 009; #56 | open |
| Owner guidance for after the call | Nothing reads it until the technician summary is built (spec S-43, code only marks it pending) | #56 build | open |
