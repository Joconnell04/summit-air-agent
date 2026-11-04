# Template vs tenant

The agent is built as a template for any home-services trade, with Summit Air as the first tenant. A tenant is one company: its facts, area, jobs and rules. Adapting the build for a roofing or kitchen remodeling company should mean writing a new tenant folder and an FSM adapter if they don't use ServiceTitan, not changing the template.

Status: version 3, which adds the trade layer (below). Version 2 came with the tool server. The mock ServiceTitan's template vs tenant split is in `servicetitan_mock/README.md`; the tool server's is in `app/README.md`, and `tests/tool_server/test_ts_platform.py` checks that no Summit Air fact is in `app/` and serves a second tenant from a copied folder. Each build lane keeps this table current; a reviewer flags any tenant fact found in template code.

## What lives where

| Part | Template (shared code, same for every company) | Tenant (per company, in `tenants/<company>/`) |
|---|---|---|
| Phone and voice | Voice worker, turn settings, the hang-up and handoff gates | The phone number, voice choice, greeting, hours |
| Conversation | Prompt structure and the rules every call follows (one question at a time, read-backs, disclosure, relevance guard) | Company name, services offered, how they describe themselves, the words callers use for their problems |
| Intake | The intake fields every job needs (who, where, what's wrong, since when, access, contact on site, who approves) | Trade-specific questions (HVAC: system type, thermostat reading; roofing: leak location, roof age, storm damage) |
| Priority and safety | The priority engine: fixed list, score, rules that can only raise priority, the safety-script gate | The trade's signals and at-risk rules (`trades/hvac/`: gas, CO, no heat with a vulnerable person; roofing would have active leak, structural damage), then the company's own safety script and local cutoffs |
| Customers and members | Lookup by phone then address, repeat-caller linking, member recognition | Membership plans and their perks |
| Booking | Idempotent booking, slot offering, read-back gate, spoken confirmation | Job types, arrival windows, capacity rules, service-area ZIPs, fees if any |
| Who gets told | Flags, handoffs, on-call texts | Who the on-call person is, office hours, after-hours rules |
| Commercial and out of scope | Decline-and-log flow | What counts as out of scope (Summit Air: commercial) |
| Systems | The FSM adapter interface and the mock ServiceTitan | Which adapter and credentials (ServiceTitan, Housecall Pro, Jobber, or a sheet) |
| Company knowledge | How the agent reads a knowledge folder and uses it in answers | `tenants/<company>/knowledge/*.md`: slogan or sign-off, plan descriptions, equipment brands they service, common questions |
| Local context | How area facts are used in the conversation; how local names reach the voice and the ears (`voice/names.py`) | The boroughs, neighborhoods, housing types and local rules (NYC heat law); `names.yaml`, the street names locals say differently from their spelling |
| Evals | Harness, scoring, gates, the persona structure | The scenarios and caller personas for that trade |

## Trade layer

Between the template and the company sits the trade: what every company in one trade shares. A tenant's `agent.yaml` starts with `extends: hvac` and is laid over `trades/hvac/agent.yaml`; `app/tenant.py` (`load_agent`, `merge`) does it when the server loads the tenant, so the server sees one merged config. `uv run python -m app.tenant tenants/summit-air` prints it.

How the two files merge:

- Mappings merge key by key, and the tenant's value wins.
- A list of mappings that all carry an `id` (the priority rules) merges by id: a tenant entry with a trade id is merged into that rule, and new ids come after the trade's. Summit Air uses this once, adding `age_at_least: 60` to the trade's `vulnerable_person` rule.
- Any other list (phrases, job types) is the trade's items, then the tenant's new ones.

What a tenant can and can't change. The server refuses to load a tenant that breaks these, so the mistake shows at start, not on a call:

- Can: add phrases to any trade list (safety signals, vague phrases, a rule's phrases), add job types or new conditions to a trade rule, add its own rules, raise a trade rule's `raise_to` or `raise_to_when_mild`, raise a job type's `base` level, and set anything the trade leaves out.
- Can't: drop a trade phrase or rule; lower a trade rule's level or a job type's base level; change any other value the trade set on a rule (turning off `group` or `safety_signal`, say); add `safety.denials`, since a denial turns the answer to a safety check into a no (Summit Air has no denials of its own, so all of them are the trade's); replace a trade list or mapping with another shape; mix entries with and without an `id` in a rules list. Loosening any of these is a change to the trade, reviewed like any rule change.
- Must: give every safety kind its `lines` and `variants`, every vague kind its check `question`, and the `sign_off` and `flag_reason` lines. The trade lists the signals but none of the words, so these are checked when the tenant loads.

`trades/hvac/agent.yaml` holds the safety signal phrases (gas and carbon monoxide, the vague smells and symptoms that get a check question, the denials), the priority rules for who is at risk with each job type's default level, and which job types and rules the after-hours rules apply to, and `equipment_words`, the trade's equipment as callers name it, so swearing at a broken system isn't read as anger at the call when deciding a cold handoff (app/transfer.py).

The move was kept narrow. These stay with the tenant even though another HVAC company might share them:

- What the agent says: the safety lines, the check questions, the safety sign-off. Each company signs off its own script (docs/decisions/24-safety-lines-signoff.md), so a trade edit must never change words a company already signed.
- Numbers sourced to one city: the temperature brackets, the age 60 line and the two-risk-factor rule (both NYC Health), the after-hours temperature cutoffs.
- The commercial phrases, which follow Summit Air's choice to serve homes only and NYC housing types.
- `config.yaml` as a whole: it is the company as its own ServiceTitan account knows it, ids included, and the mock reads it directly.
- `knowledge/`, the `voice` lines and `names.yaml`.

Checks: `tests/tool_server/test_ts_trades.py` holds Summit Air's merged config to a golden copy of what the server read before the split (`tests/tool_server/golden/summit-air-agent.yaml`; a trade edit that changes Summit Air shows up there), checks that the trade folder names no company or place, loads a tenant that inherits the trade with one override, and tries each change a tenant can't make. The eval run hash covers `trades/` as well as `tenants/` (`evals/fingerprint.py`).

## Owner edits

An owner can change six wording lines and add short guidance for four call stages from the owner view (app/rules.py, spec S-51). What contains those edits, in order of how much it is relied on:

1. **The server gates.** The read-back, the safety lines, residential only, the service area, priority only going up and every other must-happen rule are enforced in server code on every call, whatever the prompt says. No edit reaches them: a draft can only name the editable lines and stages, and a stored version is validated again before a call uses it.
2. **The eval gate.** A draft goes live only after every must-pass scenario passes on it (evals/gate.py), with one rerun of a scenario that misses.
3. **The guidance check, last and least.** When a draft is saved, one model call (app/guidance_check.py, prompt `prompts/guidance-check.md`, profile `owner_edits.guidance_check`) reads each new or changed note and line against a short list of the locked rules built from the rulebook, and refuses only one that contradicts a locked rule or tells the agent to skip or override one, with the reason shown on the editor. A note that merely mentions a subject ("if someone mentions 911, stay calm and polite") passes; no single word refuses a note. If the check can't run (no key, a timeout, an answer that doesn't cover every item), the save is refused with "Couldn't check this right now, try again", never let through unchecked. It is a model's judgement, so it can miss a determined rewording. That is why it is not what keeps a caller safe.

Some checks are structural, not about meaning, and stay in code: text is folded to plain ASCII with NFKC, so hidden or look-alike characters can't carry anything; guidance has no template or markup characters (braces, brackets, `#`, backticks, angle brackets); lines keep their exact `{fields}`; the length and per-stage limits hold; and a wording line whose default says the AI disclosure keeps it. A tenant's `agent.yaml` lists that disclosure phrase under `owner_rules.disclosure`.

## How a new company is added

1. Copy `tenants/summit-air/` to `tenants/<company>/` and edit it: `config.yaml` (the company as its field service system knows it), `agent.yaml` (keep `extends: hvac` for an HVAC company and write what differs: dialed numbers, the safety lines, temperature brackets, after-hours cutoffs, callback owners, retention, the lines the agent says), `names.yaml` (local names, or delete it), `knowledge/*.md` and the mock's `seed.py`. A company in a new trade starts by writing `trades/<trade>/agent.yaml`.
2. Write or reuse an FSM adapter.
3. Write that trade's persona scenarios and run the eval suite.
4. Point a phone number at the tenant. One running deployment serves several tenants; the dialed number picks which one.
