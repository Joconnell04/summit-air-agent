# Template vs tenant

The agent is built as a template for any home-services trade, with Summit Air as the first tenant. A tenant is one company: its facts, area, jobs and rules. Adapting the build for a roofing or kitchen remodeling company should mean writing a new tenant folder and an FSM adapter if they don't use ServiceTitan, not changing the template.

Status: version 2, updated with the tool server. The mock ServiceTitan's template vs tenant split is in `servicetitan_mock/README.md`; the tool server's is in `app/README.md`, and `tests/tool_server/test_ts_platform.py` checks that no Summit Air fact is in `app/` and serves a second tenant from a copied folder. Each build lane keeps this table current; a reviewer flags any tenant fact found in template code.

## What lives where

| Part | Template (shared code, same for every company) | Tenant (per company, in `tenants/<company>/`) |
|---|---|---|
| Phone and voice | Voice worker, turn settings, the hang-up and handoff gates | The phone number, voice choice, greeting, hours |
| Conversation | Prompt structure and the rules every call follows (one question at a time, read-backs, disclosure, relevance guard) | Company name, services offered, how they describe themselves, the words callers use for their problems |
| Intake | The intake fields every job needs (who, where, what's wrong, since when, access, contact on site, who approves) | Trade-specific questions (HVAC: system type, thermostat reading; roofing: leak location, roof age, storm damage) |
| Priority and safety | The priority engine: fixed list, score, rules that can only raise priority, the safety-script gate | What counts as urgent and the safety scripts themselves (HVAC: gas, CO, no heat with a vulnerable person; roofing: active leak, structural damage) |
| Customers and members | Lookup by phone then address, repeat-caller linking, member recognition | Membership plans and their perks |
| Booking | Idempotent booking, slot offering, read-back gate, spoken confirmation | Job types, arrival windows, capacity rules, service-area ZIPs, fees if any |
| Who gets told | Flags, handoffs, on-call texts | Who the on-call person is, office hours, after-hours rules |
| Commercial and out of scope | Decline-and-log flow | What counts as out of scope (Summit Air: commercial) |
| Systems | The FSM adapter interface and the mock ServiceTitan | Which adapter and credentials (ServiceTitan, Housecall Pro, Jobber, or a sheet) |
| Company knowledge | How the agent reads a knowledge folder and uses it in answers | `tenants/<company>/knowledge/*.md`: slogan or sign-off, plan descriptions, equipment brands they service, common questions |
| Local context | How area facts are used in the conversation | The boroughs, neighborhoods, housing types and local rules (NYC heat law) |
| Evals | Harness, scoring, gates, the persona structure | The scenarios and caller personas for that trade |

## How a new company is added

1. Copy `tenants/summit-air/` to `tenants/<company>/` and edit it: `config.yaml` (the company as its field service system knows it), `agent.yaml` (dialed numbers, safety lines and signals, priority rules, after-hours cutoffs, callback owners, retention, the lines the agent says), `knowledge/*.md` and the mock's `seed.py`.
2. Write or reuse an FSM adapter.
3. Write that trade's persona scenarios and run the eval suite.
4. Point a phone number at the tenant. One running deployment serves several tenants; the dialed number picks which one.
