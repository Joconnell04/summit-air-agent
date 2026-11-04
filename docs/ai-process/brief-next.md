# Brief: for after the live calls

Started while Jackson steps away. Each section is either a decision that needs him or an obvious call already made and listed here so he can see it. Updated at each check-in.

## Needs Jackson

2. **The 10 live calls,** once the batch below is merged and deployed.

## Decided without Jackson (obvious calls)

- **Eval floor: no reset needed.** With round 3 and 4 fixed, PR #82's run scored 91% under the stricter grader with every must-pass passing, so the 90% floor stands.

- The side model is switched off on the server until a with-and-without eval check clears it; the evals had always run without it (check running, under $3).
- Speaking rate (PR #83): review found false triggers ("you don't need to speak slower", "send someone faster", "my heat is slow, I live on Main Street"); sent back to tighten before merge.
- Recording-notice wording lives in the tenant folder, not app code, so one eval reference is re-adopted after the batch rather than per PR.

## In progress

| Lane | PR | State |
|---|---|---|
| Round 3 and 4 wording, read-back and booking spoken by the server | #82 | implementing round 4, then one eval run |
| End-of-turn wait, timer fix, stage times, voice tweaks | none yet | rig runs |
| Speaking rate per call | #83 | fixing review findings |
| Accounts, roles, settings panel | #85 | done; merges in the batch with one eval re-adopt |
| Simulated-call recordings and listener | none yet | building |
| Two calls, one slot | none yet | building |
| Debug bench | none yet | building |
| Cold handoff when a caller demands a person | none yet | building |
