# Scenarios

One YAML file per simulated call, named `<id>.yaml`. `evals/scenario.py` validates them and a test loads every file, so a typo fails `make check`.

## Fields

| Field | Meaning |
|---|---|
| `id` | Same as the file name: lowercase words joined by hyphens |
| `flow` | One or more of the flow labels in evals/README.md |
| `difficulty` | easy, standard, rigorous or adversarial (evals/README.md) |
| `traits` | Caller traits from evals/README.md, such as stutter, late-correction, split-digits |
| `journeys` | Call journeys exercised: `J1` to `J14` (docs/call-journeys.md), `spine`, or `tool-failure` |
| `spec` | Spec lines the scenario tests (docs/spec.md). Any **M** line makes it a must-pass case |
| `tags` | Extra tags; `smoke` puts it in the smoke suite |
| `caller_id` | `unknown` (a number not on file, the default), `seed:<key>` for a caller the mock seeds, or a phone number |
| `time` | Local New York time the call happens, default `2026-11-02T09:00` (a Monday, open). The mock is seeded for the two weeks from 2026-11-02 |
| `persona` | Who is calling and how they talk, in two or three sentences |
| `hidden_facts` | What the caller knows and gives only when asked: name, address, phone, the problem |
| `opening` | The caller's first line, word for word |
| `script` | How the caller behaves, as short instructions to the simulated caller |
| `max_turns` | Caller turns before the run is marked `error` (default 16) |
| `must` | Code checks; every one has to pass |
| `should` | What the grader looks for in how the call felt |

## Seeded callers

`caller_id: seed:<key>` takes the phone number from the mock's seed (`servicetitan_mock/README.md`); `seed:commercialAccounts.1` picks an item from a list. The addresses below are fixed by the seed.

| Key | Who | On file |
|---|---|---|
| `repeatCallerOpenJob` | Maria Delgado, an open no-heat job today, gate code note | 620 Bedford Ave, Brooklyn 11235 |
| `jobDoneLastWeek` | Thomas Brennan; his note asks to skip the read-back | 800 Springfield Blvd, Queens 11368 |
| `activeMemberWithAdultChild` | Linda Park, active member; `adultChildPhone` is her son David | 689 Springfield Blvd, Queens 11432 |
| `expiredMember` | Frank Moretti, plan expired, hard of hearing | 290 Bedford Ave, Brooklyn 11218 |
| `storedActiveButLapsed` | Gloria Santos, plan stored as active but past its end date | 809 Union St, Brooklyn 11206 |
| `sharedPhone` | Hector and Marisol Ruiz share one number | 507 Coney Island Ave, Brooklyn 11210 |
| `ownerAndTenant` | `ownerPhone` Dennis Ocasio, `tenantPhone` Priya Nair (unit 2, landlord approves) | 801 Fenimore St, Brooklyn 11206 |
| `outOfArea` | Gerald Whitman | 47 Willis Ave, Mineola 11501 |
| `commercial` | Harbor Bakery Group | 232 Lexington Ave, Brooklyn 11221 |
| `commercialAccounts.N` | 0 bakery, 1 property manager, 2 restaurant, 3 school, 4 dental office, 5 co-op board | |
| `replacementLead` | Naomi Feld, asking about a replacement | |

Service-area ZIPs are in `tenants/summit-air/config.yaml` (Brooklyn, Queens, Staten Island). Use made-up 555-01xx numbers for unknown callers.

## Checks

Each `must` item is `- <check>: <argument>`. They read the tool server's record of the call (`evals/checks.py`).

| Check | Argument | Passes when |
|---|---|---|
| `safety_lines` | `said_first` | a safety signal fired, the agent's next line was the fixed lines word for word, and a person was flagged |
| | `not_said` | no signal fired and the agent never said any safety lines |
| `safety_check` | `asked` / `cleared` | the vague-smell question was asked / the caller's clear no cleared the call |
| `booking` | `none`, `one`, or `{count, priority, zip, job_type, seek_earlier, safety_override}` | the bookings made match |
| `read_back` | `before_booking` | no booking came before a confirmed read-back |
| `flag` | `person`, `none`, `safety`, `safety_check_cleared` | a person flag was raised / none was / a flag of that kind exists |
| `lead` | `commercial`, `membership`, `install` | a lead of that kind was recorded |
| `out_of_area` | `recorded` or a ZIP | an out-of-area ZIP was recorded |
| `tool_ok` | a tool name or list | each tool was called and succeeded |
| `tool_not_called` | a tool name or list | none of them was called |
| `no_changes` | `true` | no booking, reschedule, cancel or do-not-call change |
| `outcome` | a call outcome or list | the call's outcome (`booked`, `safety`, `declined_commercial`, `declined_out_of_area`, `changed_booking`, `callback`, `flagged`, `no_booking`) |
| `ended_by` | `caller` or `agent` | who ended the call |
| `callback` | `promised` or `none` | a timed callback was stored, or none |
| `agent_says` | a regex or list | the agent said each one at some point (case ignored) |
| `agent_never_says` | a regex or list | the agent never said any of them |

Job types: `no_heat`, `no_cooling`, `tune_up`, `safety_check`, `estimate`. Priorities: Low, Normal, High, Urgent.
