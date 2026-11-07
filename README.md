# Summit Air inbound agent

An AI phone agent that answers inbound calls for Summit Air, a 40-tech residential HVAC company in NYC. It works out what's wrong, checks whether the caller is residential and a member, collects what a technician needs, separates routine visits from urgent ones, and books the visit or sets the next step.

## Start here

- **What's live.** Call (516) 212-9051. Retell hears the caller and speaks; the turn runs on our server through Retell's custom-LLM socket (`voice/retell_llm/`), which calls the tool server (`app/`), which books into a mock ServiceTitan (`servicetitan_mock/`). Claude Haiku 5.5 writes the replies; the server speaks every sentence that has to be exact.
- **Where the rules live.** In layers, each with one job: the server's code holds every rule that must happen (safety lines, priority, the read-back before booking, dispatch); company and trade config hold the wording, seasons and roster; the prompt holds how Brian talks; a side model only ever raises priority; the socket and a reply filter guard what gets said. One caller line through every layer, and the if-then tree on a no-heat or gas call: [docs/rule-layers.md](docs/rule-layers.md).
- **How it's tested.** Each bug found on a phone call became a test that names the call, so the suite (about 3,700 tests, `make check`) reads as the list of things that went wrong once. Above it: simulated callers through the real socket ([evals/](evals/README.md)), scripted phone calls on the live number, and A/B comparisons judged by hand ([evals/rounds/](evals/rounds/README.md)).
- **What it doesn't do yet.** Spanish, quoting prices (never, by design), commercial bookings (taken as a lead for a person), and outbound callbacks dialed by the agent itself. Known rough edges from the last test round: it sometimes asks the system type after the caller named it, and about one symptom call in six hears "is it still working?" after already saying so. The full list: [docs/later.md](docs/later.md).

**How it's built.** Retell runs the phone line: listening, turn-taking, backchannels and the voice ("Brian"). Our tool server owns the facts and the rules: lookups, the read-back before booking, the service area, the residential check, priority, emergency dispatch, identity checks and the safety lines. The turn itself runs on our server through Retell's custom-LLM socket (DECISIONS.md 121, `voice/retell_llm/` and `app/flow.py`): the server speaks the sentences that must be exact (the read-back, slot offers, the booking line, safety, the greeting and the goodbye) and Claude Haiku 5.5 writes the rest. The socket build passed the done bar on 24 scripted phone calls, 23 of them clean, with no safety errors (DECISIONS.md 119 and 126, [evals/rounds/socket-buildout-01.md](evals/rounds/socket-buildout-01.md)). The hosted single-prompt Retell agent it replaced stays as the fallback, one phone-number switch away. Two small OpenAI calls sit beside the conversation: a side model (OpenAI Decisions) answers one yes-or-no question about a caller's line (is someone at risk at home, is there a danger right now), and only ever raises priority or brings the safety lines; and an embedding index finds the passage that answers an equipment question (DECISIONS.md 131, 132).

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

Call it at (516) 212-9051 (DECISIONS.md 127). A caller who asks for a person is put through to the office phone, or to a hold line when the owner view's Settings has Hold line on. The owner view is at https://45-77-105-247.sslip.io/owner and the debug bench at https://45-77-105-247.sslip.io/bench (both need a login).

**How we got here.** We tried several stacks and gave each a fair test: a layered custom stack (LiveKit, Deepgram, Claude, Cartesia), Retell native, OpenAI's full-duplex model and others, then four agent arms side by side on the real phone line. The layered stack's own seams caused the stalls; the hosted model kept rewording sentences the server owned. The answer was Retell for the voice and our server for every word that has to be right. The story in order: [DECISIONS.md](DECISIONS.md) 111, 112, 121 and 124, the [simplicity slate](evals/rounds/simplicity-slate-01.md) and the [side-by-side](evals/rounds/side-by-side-01.md).

## The six requirements, where each is enforced and how it's tested

| The assignment asks the agent to | Enforced in | Tested by |
|---|---|---|
| Answer and figure out the HVAC issue | The server's step order (`app/flow.py`; the hosted fallback's is in `retell/summit-air-jackson/prompt.md`) and the trade's job types (`trades/hvac/`) | `tests/tool_server/test_ts_booking_flow.py`, `test_ts_trades.py` |
| Tell residential from commercial | `record_lead` kind `commercial` and the booking gate in `app/tools.py`; the company's rule in `tenants/summit-air/` | `test_ts_booking.py`, `test_ts_calls.py` |
| Know whether the caller is a member | Caller-ID lookup before hello (`app/lookup.py`, `prompts/assemble.py` call notes) and `find_customer` | `test_ts_lookup.py`, `test_ts_accounts.py` |
| Collect what a technician needs | `book_appointment`'s required details and the job summary (`app/booking.py`), the access question and the catch-all answer | `test_ts_booking.py`, `test_ts_summary.py` |
| Separate routine from urgent (no heat in winter, no AC with a medical condition or an older person, gas) | Priority from the caller's own words (`app/priority.py`), safety lines and gates (`app/safety.py`), emergency dispatch, and one Emergency dispatch, Scheduled or No visit label per call (`app/labels.py`) | `test_ts_priority.py`, `test_ts_priority_caller_words.py`, `test_ts_safety.py`, `test_ts_dispatch.py`, `test_ts_labels.py` |
| Book the visit or confirm next steps | `read_back`, `find_slots`, `book_appointment` against the CRM, with `flag_person`, `record_lead` and the warm transfer for everything else | `test_ts_booking.py`, `test_ts_handoff.py`, `test_ts_transfer.py` |

Every rule that must happen lives in server code, never only in the prompt; a flag the model can set is not a gate.

## The prompt

- The agent's prompt and every version of it, with a note on why each change was made and whether it held: [retell/PROMPT-LOG.md](retell/PROMPT-LOG.md).
- The hosted fallback's prompt, tools and settings, snapshotted after every change: [retell/summit-air-jackson/](retell/summit-air-jackson/).
- The lines the server speaks itself, word for word: `tenants/summit-air/agent.yaml`.
- The socket agent's prompt: [retell/socket/prompt.md](retell/socket/prompt.md).

## Debug a call

1. Find the call: the owner view's Calls page, or the bench (`/bench`), which filters by outcome, label, who ended the call and signals (refused, person, transfer, hold, after hours, commercial). The bench's call page has six tabs: Summary (what the call achieved: the booking or callback, the customer matched by phone or added by the booking, the details captured), Why (each server decision, what happened and why), Transcript (the server's lines beside what Retell heard, talked-over lines marked, and each tool call under the line it followed, opening to its arguments and to a page with its result and the records it wrote, the ServiceTitan job read live), Timing (each reply's stages), Steps (the step graph and rule proof) and Replay; its Log button opens the call's JSON log behind the bench's own sign-in. The bench's Questions page answers the owner's likely questions from the calls on record, and its Schedule shows what find_slots offers before and after a block or a cancelled job.
2. `make trace CALL=<id>` prints its timeline: each caller line, each tool call with its arguments and result, and what was said.
3. The bench's **Checks** tab runs the Opus failure grader on one call, the past hour or the past day, and flags only real failures (DECISIONS 120).
4. Retell's own record of the call (`GET /v2/get-call/<id>`) has the audio, per-turn latency and the transcript with tool calls.

## Find your way

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
- [docs/history/from-other-projects/](docs/history/from-other-projects/README.md): lessons carried in from earlier builds; "Bobert" there is an earlier personal practice project, not a person

## Code

The agent first, then the server it calls, then how it's tested and run. Earlier agent arms are kept in `retell/archive/`; the voice stacks that were tried and dropped (ElevenLabs, OpenAI Realtime, Gemini Live, Retell native) are on branch `archive/voice-stacks`.

| Folder | What it is |
|---|---|
| **The agent** | |
| [retell/socket/](retell/socket/) | The live agent's prompt, tools and push scripts: the socket agent on (516) |
| [voice/retell_llm/](voice/retell_llm/README.md) | The custom-LLM socket Retell talks to: speaks the server's exact sentences, Haiku writes the rest |
| [voice/retell/](voice/retell/README.md), `voice/retell_flow/` | The bridge between Retell and the tool server: caller ID and call context before hello, the call's start and end |
| `retell/summit-air-jackson/` | The hosted single-prompt agent kept as the fallback, snapshotted after every change, with its prompt log in [retell/PROMPT-LOG.md](retell/PROMPT-LOG.md) |
| `retell/archive/` | Earlier agent arms (Sonnet, behavioral Haiku, server-led), kept as the reference (DECISIONS.md 112, 121, 124) |
| [voice/livekit/](voice/livekit/README.md) | The first phone stack (LiveKit, Deepgram, Claude, Cartesia). No number rings it now; it runs the agent in process for the text evals and the rules gate |
| **The server** | |
| [app/](app/README.md) | The tool server every call talks to: gates, priority, safety, booking, transfers, the owner view and the debug bench |
| `tenants/summit-air/`, `trades/hvac/` | The company's own facts, area and wording, over the HVAC trade's rules |
| [prompts/](prompts/README.md), `config/models.yaml` | The shared prompts split by job, and every model and voice setting by named profile (no model id is written anywhere else) |
| [servicetitan_mock/](servicetitan_mock/README.md) | The mock ServiceTitan the integration layer talks to |
| **Tested and run** | |
| [evals/](evals/README.md) | The eval harness, scenarios, checks, the audio rig, and each round's results |
| `tests/`, `scripts/` | The test suite, the test-count ratchet, the world reset and the lanes page |
| `deploy/` | The server, Caddy and service files |

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
