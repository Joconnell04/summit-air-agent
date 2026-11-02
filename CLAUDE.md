# Summit Air inbound agent

Inbound phone agent for a residential HVAC company. This repo is also reviewed as an interview case study, so the code and docs must read like a careful engineer wrote them.

The assignment, word for word: `docs/assignment.md`. Reread it before any design, scope or demo decision. Never edit it.

## Build standard

- Small, boring, conventional code. No clever abstractions, no "AI-powered" naming, no emoji, no banner comments.
- Comments only where the why isn't obvious.
- No em dashes in any file. Use commas, semicolons or full stops.
- Rules that must happen (safety script, priority, service area, booking gates) live in server code, never only in the prompt. A flag the model can set is not a gate.
- Every behavior change adds or updates a test. `make check` must pass before a commit; the ratchet blocks a lower test count.
- Retell API: use only the versioned endpoints (`POST /v2/list-agents`, `GET /v2/list-phone-numbers`, `/v2/...` for LLMs and flows, `POST /v3/list-calls`) and read the paginated `items` field. The unversioned list endpoints are removed and Retell emails a warning for every use.
- Prompts live in `prompts/`, split by job. Models are named profiles in `config/models.yaml`. Never hard-code a model id elsewhere.
- Reviewers have never heard of Bobert. In docs, call it "the earlier practice agent" and link from-other-projects/bobert the first time, or leave the name out.
- Numbers in docs carry their source: SOURCE (with link or file), MEASURED (with how), or ASSUMED.

## Working agreements

- Work is tracked in GitHub Issues on the "Summit Air" project board. Use `gh`.
- A build session starts from a brief in `docs/ai-process/briefs/`, works on a branch, and ends with a pull request that says `Closes #N`.
- If blocked on a choice that is the human's to make, open an issue with the needs-decision template, label `needs-decision`, put it in the Needs decision column, and continue with other work.
- Every needs-decision issue gets a readout in `docs/decisions/NN-<slug>.md` (format in that folder's README), researched in the background before Jackson is asked. GitHub issues aren't visible in the Claude app, so the readout is what he reads beside the chat; he answers in chat.
- Ideas Jackson throws out mid-step are prompts to look into, not decisions. Record them as open considerations or explorations, never write them into a doc as settled.
- Decisions go in `DECISIONS.md` as one short entry each.
- When Jackson sets something aside ("later", "come back to that", "not now"), add it to `docs/later.md` with where it came from. The list is walked with him before V1 is called done.
- High-touch docs (`docs/users.md`, `docs/call-journeys.md`, `docs/spec.md`, prompt voice, safety script) change only through `/round`, which logs the round in `docs/ai-process/coauthoring.md`.
- Side research goes through `/explore`; results land in `docs/explorations/`.

## Driving the session

Claude drives; Jackson steers. The route is docs/ai-process/phases.md. After any step closes, tick it, move its issue on the board, commit, and say "Unless there's anything else, next is <step>." Then wait. New ideas mid-step go to `/consider`; anything needing research goes to `/explore`. Keep chat short and put substance in files. Surface one decision at a time: point Jackson at the readout file (or the section of it) that holds the question and its context, take his answer, implement it, then surface the next one.

## Voice transcription glossary

Jackson often speaks through transcription. Read these as:
- "retail", "retail AI" = Retell
- "live kit" = LiveKit
- "deep gram" = Deepgram; "cartesia", "cartesian" = Cartesia
- "service titan" = ServiceTitan
- "Robert" in a build context = Bobert, the earlier agent (see from-other-projects)
- "Paul" = Paul Appleby, the Revin FDE playing the Summit Air owner
- "evals", "evil suite" = the eval suite

Log his words verbatim in co-authoring and transcripts; apply the glossary only when acting on them.

## Commands

- `make setup`, `make check`, `make ratchet-update`
- `/next` where we are, what needs Jackson, what's next
- `/consider <idea>` add to docs/considerations.md and keep going
- `/round <doc>` revise a high-touch doc from feedback and log it
- `/explore <idea>` background research into docs/explorations
- `/discuss <NNN>` load an exploration to talk through and decide
