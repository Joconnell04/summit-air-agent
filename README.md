# Summit Air inbound agent

An AI phone agent that answers inbound calls for Summit Air, a 40-tech residential HVAC company in NYC. It works out what's wrong, checks whether the caller is residential and a member, collects what a technician needs, separates routine visits from urgent ones, and books the visit or sets the next step.

**How it's built.** Retell runs the phone line: listening, turn-taking, backchannels and the voice ("Brian"). Our tool server owns the facts and the rules: lookups, the read-back before booking, the service area, the residential check, priority, emergency dispatch, identity checks and the safety lines. Today the agent on the line is a hosted Retell single-prompt agent (Claude Haiku 5.5) that calls the tool server for every fact and gate. The next build, DECISIONS.md 121, moves the turn itself onto our server through Retell's custom-LLM socket, so the server speaks the sentences that must be exact and Haiku writes the rest; it is on branch `joco/socket-buildout-176` (`voice/retell_llm/`, `app/flow.py`) until it passes the done bar (DECISIONS.md 119), and the hosted agent stays as the fallback, one phone-number switch away.

```
caller --phone--> Retell: ears, turn-taking, backchannels, voice
                    |  tool calls and call events (HTTPS)
                    v
                  tool server (app/): facts, gates, priority, safety lines, labels
                    |                         |
                    v                         v
                  CRM: mock ServiceTitan    owner view and debug bench
                  (servicetitan_mock/)      (/owner, /bench)
```

Call it at (646) 624-0418. The owner view is at https://45-77-105-247.sslip.io/owner and the debug bench at https://45-77-105-247.sslip.io/bench (both need a login).

**How we got here.** We tried several stacks and gave each a fair test: a layered custom stack (LiveKit, Deepgram, Claude, Cartesia), Retell native, OpenAI's full-duplex model and others, then four agent arms side by side on the real phone line. The layered stack's own seams caused the stalls; the hosted model kept rewording sentences the server owned. The answer was Retell for the voice and our server for every word that has to be right. The story in order: [DECISIONS.md](DECISIONS.md) 111, 112, 121 and 124, the [simplicity slate](evals/rounds/simplicity-slate-01.md) and the [side-by-side](evals/rounds/side-by-side-01.md).

## The six requirements, where each is enforced and how it's tested

| The assignment asks the agent to | Enforced in | Tested by |
|---|---|---|
| Answer and figure out the HVAC issue | The prompt's question order (`retell/summit-air-jackson/prompt.md`; `app/flow.py` in the socket build) and the trade's job types (`trades/hvac/`) | `tests/tool_server/test_ts_booking_flow.py`, `test_ts_trades.py` |
| Tell residential from commercial | `record_lead` kind `commercial` and the booking gate in `app/tools.py`; the company's rule in `tenants/summit-air/` | `test_ts_booking.py`, `test_ts_calls.py` |
| Know whether the caller is a member | Caller-ID lookup before hello (`app/lookup.py`, `prompts/assemble.py` call notes) and `find_customer` | `test_ts_lookup.py`, `test_ts_accounts.py` |
| Collect what a technician needs | `book_appointment`'s required details and the job summary (`app/booking.py`), the access question and the catch-all answer | `test_ts_booking.py`, `test_ts_summary.py` |
| Separate routine from urgent (no heat in winter, no AC with a medical condition or an older person, gas) | Priority from the caller's own words (`app/priority.py`), safety lines and gates (`app/safety.py`), emergency dispatch, and one Routine, Urgent or No visit label per call (`app/labels.py`) | `test_ts_priority.py`, `test_ts_priority_caller_words.py`, `test_ts_safety.py`, `test_ts_dispatch.py`, `test_ts_labels.py` |
| Book the visit or confirm next steps | `read_back`, `find_slots`, `book_appointment` against the CRM, with `flag_person`, `record_lead` and the warm transfer for everything else | `test_ts_booking.py`, `test_ts_handoff.py`, `test_ts_transfer.py` |

Every rule that must happen lives in server code, never only in the prompt; a flag the model can set is not a gate.

## The prompt

- The agent's prompt and every version of it, with a note on why each change was made and whether it held: [retell/PROMPT-LOG.md](retell/PROMPT-LOG.md).
- The hosted agent's prompt, tools and settings, snapshotted after every change: [retell/summit-air-jackson/](retell/summit-air-jackson/).
- The lines the server speaks itself, word for word: `tenants/summit-air/agent.yaml`.
- The socket build's prompt pieces: `prompts/` on branch `joco/socket-buildout-176`.

## Debug a call

1. Find the call: the owner view's Calls page, or the bench, both list every call with its label.
2. `make trace CALL=<id>` prints its timeline: each caller line, each tool call with its arguments and result, and what was said.
3. The bench's **Checks** tab runs the Opus failure grader on one call, the past hour or the past day, and flags only real failures (DECISIONS 120).
4. Retell's own record of the call (`GET /v2/get-call/<id>`) has the audio, per-turn latency and the transcript with tool calls.

## Start here

| If you want | Read |
|---|---|
| The assignment, word for word | [docs/assignment.md](docs/assignment.md) |
| How it was read, and the assumptions | [docs/brief.md](docs/brief.md) |
| Who calls and what each caller needs | [docs/users.md](docs/users.md), then [docs/call-journeys.md](docs/call-journeys.md) |
| What it must do, as testable lines | [docs/spec.md](docs/spec.md) |
| How it's put together | [docs/architecture.md](docs/architecture.md), [docs/tools.md](docs/tools.md) |
| The Retell agent: prompt, tools, knowledge base and settings, versioned | [retell/summit-air-jackson/](retell/summit-air-jackson/), with every setting explained in [docs/retell-knobs.md](docs/retell-knobs.md) |
| Every choice and why | [DECISIONS.md](DECISIONS.md) (newest first), or in order in [docs/ai-process/design-trail.md](docs/ai-process/design-trail.md) |
| How it's tested | [evals/README.md](evals/README.md), with each round's results in [evals/rounds/](evals/rounds/README.md) |
| How to walk an owner through it | [docs/demo/](docs/demo/README.md): five short videos and their scripts |

## Docs by topic

**Product**
- [docs/brief.md](docs/brief.md): assumptions about the assignment and what changes if each is wrong
- [docs/users.md](docs/users.md), [docs/call-journeys.md](docs/call-journeys.md), [docs/spec.md](docs/spec.md): co-authored with Jackson; they change only through `/round`
- [docs/template.md](docs/template.md): how the build splits into a trade layer (`trades/hvac/`) and a company layer (`tenants/summit-air/`)
- [docs/client-notes.md](docs/client-notes.md): points to have ready for the owner
- [docs/later.md](docs/later.md): everything set aside, and new ideas to look into

**System**
- [docs/architecture.md](docs/architecture.md): the parts and how a call moves through them
- [docs/tools.md](docs/tools.md): the contract between the voice layer and the tool server
- [docs/design-system.md](docs/design-system.md): the owner view and bench's tokens, components and rules, also saved as a Claude design system that later sessions build from ([docs/design-system-rules.md](docs/design-system-rules.md) is its short form)

**Performance and cost**
- [docs/latency.md](docs/latency.md): where the reply delay goes, stage by stage, and how each stage is measured
- [docs/tuning.md](docs/tuning.md): the settings that change how a call feels, and how each is tested
- [docs/bakeoffs.md](docs/bakeoffs.md): choices settled by measurement (platform, models, voices)
- [docs/cost.md](docs/cost.md), [docs/scaling.md](docs/scaling.md): monthly cost and what more simultaneous calls would take

**Decisions and research**
- [DECISIONS.md](DECISIONS.md): one entry per choice, newest first
- [docs/decisions/](docs/decisions/README.md): the readouts Jackson read before deciding, numbered by the GitHub issue
- [docs/explorations/](docs/explorations/README.md): background research, numbered in order
- [docs/considerations.md](docs/considerations.md): the design-phase triage record, closed; new ideas go in docs/later.md

**How it was built with AI**
- [docs/ai-process/](docs/ai-process/README.md): lanes, briefs, the co-authoring log, phases and transcripts
- [from-other-projects/](from-other-projects/README.md): lessons carried in from earlier builds; "Bobert" there is an earlier personal practice project, not a person

## Code

| Folder | What it is |
|---|---|
| [app/](app/README.md) | The tool server every call talks to: gates, priority, safety, booking, the owner view and the debug bench |
| `retell/summit-air-jackson/` | The live agent as Retell holds it, snapshotted into the repo after every change |
| [voice/retell/](voice/retell/README.md) | The bridge between Retell and the tool server: greeting and call context before hello, the safety lines, server notes, the hang-up |
| [voice/livekit/](voice/livekit/README.md) and the other `voice/` folders | The custom stack and the bake-off entrants, kept as the reference (DECISIONS.md 112) |
| [servicetitan_mock/](servicetitan_mock/README.md) | The mock ServiceTitan the integration layer talks to |
| [prompts/](prompts/README.md) | The prompts, split by job |
| `config/models.yaml` | Every model and voice setting, by named profile; no model id is written anywhere else |
| `trades/hvac/`, `tenants/summit-air/` | Trade rules, then the company's own facts, area and wording |
| [evals/](evals/README.md) | The eval harness, scenarios, checks, the audio rig and results |
| `deploy/` | The server, Caddy and service files |
| `scripts/`, `tests/` | The test-count ratchet, the lanes page, and the test suite |

## Run

```
make setup    # install, enable the pre-commit hook
make check    # lint, tests, test-count ratchet and eval floor
make server   # the tool server on :8100, talking to the mock on :8101
make eval SUITE=smoke   # paid: simulated callers against the agent (evals/README.md)
make trace CALL=<id>    # a call's timeline in plain text
make lanes    # rewrite docs/ai-process/lanes.md from GitHub
```

The rest are in the Makefile, each with a one-line comment.

## Naming

- Docs are lowercase, hyphenated, in `docs/` by topic. Numbered series keep their prefix: explorations `NNN-` in order, decision readouts `NN-` after their GitHub issue, eval rounds `<topic>-NN` (the four co-authoring label rounds are `01-` to `04-`).
- Numbers carry their source: SOURCE (with a link or file), MEASURED (with how), or ASSUMED.
- Work is tracked in GitHub Issues: every lane has an issue labeled `agent`, and [docs/ai-process/lanes.md](docs/ai-process/lanes.md) lists them with their pull requests.
