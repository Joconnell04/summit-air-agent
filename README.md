# Summit Air inbound agent

A phone agent that answers inbound calls for Summit Air, a 40-tech residential HVAC company in NYC. It works out what's wrong, checks whether the caller is residential and a member, collects what a technician needs, tells routine visits from urgent ones, and books the visit or sets up the next step.

## Try it

- Call **(516) 212-9051**. The agent is Brian.
- The owner view is at https://45-77-105-247.sslip.io/owner and the debug bench at https://45-77-105-247.sslip.io/bench. Both need a login, shared on request.
- The demo deck is [summit-air-demo-deck.pptx](summit-air-demo-deck.pptx).

## How it works

```
caller ──phone──> Retell (listens, takes turns, speaks as Brian)
                    │  every turn, over a WebSocket
                    v
                  voice/retell_llm/  the socket: says the server's exact lines, Claude Haiku 5.5 writes the rest
                    │
                    v
                  app/  the tool server: every rule, every booking, the owner view and the bench
                    │
                    v
                  servicetitan_mock/  customers, memberships, jobs, dispatch
```

Retell handles the phone line and the voice. Each turn comes to our server, which decides what happens next and speaks the lines that have to be exact: the greeting, the safety lines, the read-back of the address and phone, the times offered, the booking and the goodbye. Claude Haiku 5.5 writes the replies in between. A small side model answers one yes-or-no question about a caller's line (is someone at risk, is there a danger now) and can only raise priority.

Any rule that has to happen lives in server code, not in the prompt. A flag the model can set is not a gate.

The hosted single-prompt Retell agent this replaced is kept as a fallback, one phone-number switch away.

## The six requirements

| The assignment asks the agent to | Where it's enforced | Tests |
|---|---|---|
| Answer and figure out the HVAC issue | The server's step order (`app/flow.py`) and the trade's job types (`trades/hvac/`) | `test_ts_booking_flow.py`, `test_ts_trades.py` |
| Tell residential from commercial | The booking gate in `app/tools.py`; a commercial caller becomes a lead for the office | `test_ts_booking.py`, `test_ts_calls.py` |
| Know whether the caller is a member | Caller-ID lookup before hello (`app/lookup.py`) and `find_customer` | `test_ts_lookup.py`, `test_ts_accounts.py` |
| Collect what a technician needs | `book_appointment`'s required details and the job summary (`app/booking.py`) | `test_ts_booking.py`, `test_ts_summary.py` |
| Separate routine from urgent | Priority from the caller's own words (`app/priority.py`), the safety lines (`app/safety.py`), emergency dispatch and the call label (`app/labels.py`) | `test_ts_priority.py`, `test_ts_safety.py`, `test_ts_dispatch.py`, `test_ts_labels.py` |
| Book the visit or confirm next steps | `read_back`, `find_slots` and `book_appointment` against the CRM; `flag_person`, `record_lead` and the transfer for everything else | `test_ts_booking.py`, `test_ts_handoff.py`, `test_ts_transfer.py` |

Which layer holds which rule (server code, company and trade config, the prompt, the side model, the socket), with one caller line traced through all of them: [docs/rule-layers.md](docs/rule-layers.md).

## How it's tested

- **Unit and integration tests** (`make check`, more than 3,700). Most bugs found on a phone call became a test named after that call.
- **Scripted phone calls** on the live number. The three demo journeys (a routine booking, no heat with an older person at home, a gas smell) passed 24 calls with no safety errors: [evals/rounds/socket-buildout-01.md](evals/rounds/socket-buildout-01.md).
- **Simulated callers and side-by-side comparisons**, with each round's results in [evals/rounds/](evals/rounds/README.md).

## What it doesn't do yet

Spanish, texts, and callbacks the agent dials itself (staff call back). It runs against a mock of ServiceTitan built from its API specs, not a live account. It never quotes prices, by design.

Rough edges seen on test calls, none of which changes a booking or the safety lines:
- It sometimes asks the system type after the caller already named it.
- About one symptom call in six hears "is it still working?" after saying so.
- A business caller who then reports gas is offered a transfer it can't make.

The full list of ideas and known issues, kept as a scratch pad during the build: [docs/later.md](docs/later.md).

## Debug a call

1. Open the call on the bench (`/bench`). The tabs: Summary (what the call achieved), Why (each server decision), Transcript (each tool call opens to its arguments, its result and the records it wrote), Timing, Steps (the call as a step graph, with the rules that fired) and Replay.
2. `make trace CALL=<id>` prints the timeline in plain text.
3. The bench's Checks tab runs a failure grader on one call or a whole day.
4. Retell's own record (`GET /v2/get-call/<id>`) has the audio and per-turn latency.

## Find your way

| If you want | Read |
|---|---|
| The assignment, word for word | [docs/assignment.md](docs/assignment.md) |
| How it was read, and the assumptions | [docs/brief.md](docs/brief.md) |
| Who calls and what each caller needs | [docs/users.md](docs/users.md), [docs/call-journeys.md](docs/call-journeys.md) |
| What it must do, as testable lines | [docs/spec.md](docs/spec.md) |
| The parts and how a call moves through them | [docs/architecture.md](docs/architecture.md), [docs/tools.md](docs/tools.md) |
| The live agent's prompt, and every change to it | [retell/socket/prompt.md](retell/socket/prompt.md), [retell/PROMPT-LOG.md](retell/PROMPT-LOG.md) |
| The lines the server speaks word for word | `tenants/summit-air/agent.yaml` |
| Every choice and why | [DECISIONS.md](DECISIONS.md), newest first |
| What was tried before this stack | [docs/bakeoffs.md](docs/bakeoffs.md), and DECISIONS.md 111, 112, 121 and 124 |
| Cost and latency | [docs/cost.md](docs/cost.md), [docs/latency.md](docs/latency.md) |
| How another company or trade is added | [docs/template.md](docs/template.md) |
| How it was built with AI | [docs/ai-process/](docs/ai-process/README.md) |
| Lessons from earlier builds | [docs/history/from-other-projects/](docs/history/from-other-projects/README.md) ("Bobert" there is an earlier personal practice project, not a person) |

## Code

| Folder | What it is |
|---|---|
| [voice/retell_llm/](voice/retell_llm/README.md) | The socket Retell talks to on every turn |
| [retell/socket/](retell/socket/) | The live agent's prompt, tool descriptions and push scripts |
| [voice/retell/](voice/retell/README.md) | The bridge for caller ID before hello, call start and call end |
| [app/](app/README.md) | The tool server: rules, priority, safety, booking, transfers, the owner view and the bench |
| `tenants/summit-air/`, `trades/hvac/` | The company's own wording, area and roster, over the HVAC trade's rules |
| [prompts/](prompts/README.md), `config/models.yaml` | Shared prompts, and every model setting by named profile |
| [servicetitan_mock/](servicetitan_mock/README.md) | The mock ServiceTitan |
| [evals/](evals/README.md) | The eval harness, scenarios and results |
| `retell/summit-air-jackson/` | The hosted fallback agent, snapshotted after every change |
| [voice/livekit/](voice/livekit/README.md) | The first phone stack, now used only to run the agent in process for text evals |
| `tests/`, `scripts/`, `deploy/` | Tests, tooling and the server setup |

Stacks that were tried and dropped are on the `archive/voice-stacks` branch.

## Run

```
make setup    # install and enable the pre-commit hook
make check    # lint, tests and the test-count ratchet
make server   # the tool server on :8100, with the mock on :8101
make trace CALL=<id>    # a call's timeline in plain text
make eval SUITE=smoke   # paid: simulated callers against the agent
make live     # put committed main live; stops if a call is on the line
```

Numbers in the docs carry their source: SOURCE (with a link or file), MEASURED (with how), or ASSUMED.
