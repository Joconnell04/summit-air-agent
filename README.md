# Summit Air inbound agent

An AI phone agent that answers inbound calls for Summit Air, a 40-tech residential HVAC company in NYC. It works out what's wrong, checks whether the caller is residential and a member, collects what a technician needs, flags urgent calls, and books the visit or sets next steps.

It runs on two parts. A Retell agent handles the phone line, listening, turn-taking and the voice, with Claude Haiku 5.5 on a short prompt. Our tool server owns every fact and every rule: lookups, the read-back before booking, the service area, the residential check, priority, identity checks and the safety lines are server code, not prompt instructions. The model asks; the server decides.

Call it at (646) 624-0418. The owner view is at https://45-77-105-247.sslip.io/owner and the debug bench at https://45-77-105-247.sslip.io/bench (both need a login).

**How we got here.** We tried several stacks and gave each a fair test. Most of the build went into a layered custom stack (LiveKit, Deepgram, Claude and Cartesia, with a side model, stage-scoped rules and a speech filter), because owning every layer promised more control. On the audio rig and on live calls, the layering itself caused the stalls and repeats, while Retell's integrated stack answered faster and sounded better. So the voice moved to Retell and the rules stayed in our server. The custom stack stays in the repo as the reference. The full story: [DECISIONS.md](DECISIONS.md) 111 and 112, and the [simplicity slate](evals/rounds/simplicity-slate-01.md).

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
