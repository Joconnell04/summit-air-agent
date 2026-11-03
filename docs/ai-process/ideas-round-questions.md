# Questions from the post-phase-2 ideas

Jackson raised these ideas after phase 2, from working with the built agent (C-54 to C-56, and a new angle on C-37), and they were researched in explorations 009 to 011 before phase 3 testing began. Folding them in at this point meant the design changed before the eval floor was set, so nothing tested had to be retested.

**Answered:** Jackson approved every recommendation below. The decisions are DECISIONS.md 020 to 023.

## Vapi (exploration 010, section 5)

1. **Vapi in the bake-off.** Recommend a documented comparison plus one test call after the main run (about $0.20, ASSUMED). Vapi can speak a tool result word for word, which Retell can't, so the test call is about whether that holds on a gas call. Becoming a full entrant would cost about $3 and 2 to 3 days.
2. **Number for that call.** Recommend Vapi's free number, which needs no Twilio change.
3. **Templates by trade and client.** Recommend a `trades/hvac/` layer in the repo that each tenant folder extends, not Vapi assistants. Vapi has no inheritance between assistants, and building there would push rules into the prompt.

## Owner-editable rules (exploration 009, section 5)

4. **How far in V1.** Recommend a read-only rulebook in the owner view first (every rule by stage, marked locked or editable, about 1.5 days, ASSUMED). Then, behind an eval gate, soft guidance per stage plus edits to four wording lines, about 6.5 days in total (ASSUMED).
5. **Can any screen loosen a gate?** Recommend never. Gates change only through engineers and `/round`.
6. **Who edits and publishes.** Recommend a separate edit login. The owner publishes once the must-pass evals pass.
7. **Where edits live.** Recommend a versioned SQLite table over the YAML defaults. That gives an audit trail and rollback without the server committing to git.
8. **Showing it in the demo.** Recommend showing a version already in the history with its gate result, not a live edit. A live run would mean minutes of waiting.
9. **Who decides the call stage.** Recommend the server, not the model.

## Pronunciation (exploration 011, section 5)

10. **Which Kosciuszko the voice says.** Recommend koss-ee-OSS-koh, the reading a Brooklyn guide calls most common; when hearing, accept all three common readings. You could also ask Paul.
11. **List size.** Recommend the 15 names that have a source, adding more as real calls turn up misses.
12. **Deepgram keyterms for hearing the names.** Recommend turning them on now, about $12 a month at assumed volume (SOURCE: deepgram.com/pricing).
13. **The clip test.** May I generate 30 Cartesia clips to check the pronunciations (about $0.09)?

## Found along the way

- **Eval staleness check (fixed).** The check didn't cover `tenants/`, so a wording or bracket edit wouldn't have made eval results stale. PR #59 fixes it.
- **Technician summary.** The spec calls for an end-of-call summary for the technician (`docs/spec.md:43`), but the code only marks it as pending. It is not decided yet whether to build it before the demo.
