# Working playbook

How each session runs, so nothing said is lost and nothing the AI does goes unchecked.

## Sessions

| Phase | Who talks | Starts from | Ends with |
|---|---|---|---|
| Design | Jackson, by voice | kickoff prompt in `briefs/` | locked users, call journeys, spec; stack and model decisions; build briefs |
| Build (2 or 3 in parallel) | agents | one brief each | a pull request per lane, reviewed and green |
| Test and tune | Jackson plus agents | the scoreboard | eval matrix across models and prompts; human test notes |
| Demo prep | Jackson | runbook draft | drills, rehearsal, final README |

Start a new session only when the phase changes, when a session has drifted across several topics, or when a fresh context would do a self-contained job better than a long history. Every new session starts by reading CLAUDE.md and running `/next`.

## Loops

- **Co-authoring** (high-touch docs): draft, spoken feedback, `/round`, repeat, `/round <doc> lock`.
- **Side ideas**: `/explore <idea>` mid-conversation, keep going, `/discuss NNN` later, decision into DECISIONS.md.
- **Build**: brief, branch, pre-commit hook, CI and ratchet, reviewer agent, merge. Two failures become a needs-decision issue.
- **Evals**: run, sort failures by cause (prompt, tool message, server rule, the test itself), fix, rerun; each real failure becomes a permanent case.

## Human-only checks

Live calls (speakerphone, noise, slow speaker, interruptions), whether it sounds like a person, the tone of the safety script, whether the owner view reads in ten seconds, and reading failure transcripts in full.
