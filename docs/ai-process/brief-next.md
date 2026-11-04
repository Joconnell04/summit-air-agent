# Brief: for after the live calls

Started while Jackson steps away. Each section is either a decision that needs him or an obvious call already made and listed here so he can see it. Updated at each check-in.

## Needs Jackson

2. **The 10 live calls,** once the batch below is merged and deployed.

## Decided without Jackson (obvious calls)

- **Eval floor: no reset needed.** With round 3 and 4 fixed, PR #82's run scored 91% under the stricter grader with every must-pass passing, so the 90% floor stands.

- The side model is switched off on the server until a with-and-without eval check clears it; the evals had always run without it (check running, under $3).
- Speaking rate (PR #83): review found false triggers ("you don't need to speak slower", "send someone faster", "my heat is slow, I live on Main Street"); sent back to tighten before merge.
- Recording-notice wording lives in the tenant folder, not app code, so one eval reference is re-adopted after the batch rather than per PR.

- **Owner admin login.** Accounts (PR #85) replace the shared token. The server's settings file now has an admin user `jackson`; the password is in the macOS Keychain as `owner-admin-password` (`security find-generic-password -a summit-air -s owner-admin-password -w`). It takes effect at the next deploy.
- **Side model stays on** (decision 056) after a with-and-without check found no false alarms; it goes live again at the next deploy.
- **Merged while you were away:** #83 speaking rate, #85 accounts and settings, #86 booking under load, #87 the "nobody's" fix, #88 ZIP from the caller and street names.

- **Cold handoff (PR #90):** "Can I talk to someone now?" was triggering an immediate transfer; it now needs "right now", "right away" or a repeated request. Swearing about the equipment ("the damn furnace") doesn't count as anger at the call. A gas or CO caller who demands a person after the safety lines is handed off (decision 059 when it merges).
- **Debug bench (PR #89):** phone numbers on the bench keep their last four digits; the full number is never in a URL or a replay file.
- **Main went briefly red** when the ZIP rule (#88) met tests whose scripted callers never say a ZIP; the test callers now say it (branch `fix/pair-callers-say-zip`).

## In progress

| Lane | PR | State |
|---|---|---|
| Round 3 and 4 wording, server-spoken read-back and booking | #82 | 91% with every must-pass passing; merging main and re-running once to become the deploy reference |
| Debug bench, then the rule editor moved onto it | #89 | review fixes (phone number in replay ids, redaction gaps) |
| Cold handoff | #90 | review fixes ("now" alone no longer triggers it) |
| End-of-turn wait, timer fix, stage times, voice tweaks | none yet | rig runs |
| Simulated-call recordings and listener | none yet | recording the 15-call batch |
| Merged | #83, #85, #86, #87, #88, #91 | speaking rate, accounts and settings, booking under load, "nobody's", ZIP from the caller, test fixes |
