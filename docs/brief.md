# Brief

The assignment, word for word, is in `assignment.md`. This file holds how it is read: the assumptions made and what changes if each is wrong. Values marked ASSUMED live in tenant config (`tenants/summit-air/`), so changing one is an edit, not a rebuild.

## Assumptions

| Assumption | Value | If it's wrong |
|---|---|---|
| Which three counties | Brooklyn, Queens and Staten Island (ASSUMED; exploration 002: a residential-only shop fits one and two-family housing) | Change the service-area ZIPs in config; the heat-law line applies only inside the five boroughs |
| Office hours | Mon to Fri 8 a.m. to 8 p.m., Sat 8 a.m. to 4 p.m., closed Sunday (ASSUMED) | Change hours in config; after-hours handling follows |
| Arrival windows | 8 to 12, 12 to 4, 4 to 8, plus an after-hours window (ASSUMED) | Change windows in config |
| Fees | None stated: no diagnostic or after-hours fee in config (ASSUMED; Summit Air's real fees unknown) | Set the fee in config; the agent then states it only after triage |
| Membership | One plan, Summit Standard Membership: priority scheduling, two tune-ups a year, 15% off repairs, no after-hours fee. A caller is either a Summit member or not (ASSUMED perks; typical perks from exploration 001) | Edit the plan's perks in config; the agent never promises a perk the plan lacks |
| Who is on call after hours | One technician on a weekly rotation, alerted by email; the service manager is backup (ASSUMED; most common set-up, exploration 001) | Change the alert recipients; an answering service would sit in front of the agent instead |
| Call volume | About 150 calls a day, 1 to 2 at once in the busiest hour, up to about 15 at once on a bad surge morning (ASSUMED; exploration 003) | Capacity plan in docs/scaling.md scales up or down without stack changes |
| After-hours cutoffs | Tonight if inside below 50 F or falling fast or someone at risk; first morning slot if above 55 F and holding (ASSUMED; vendor practice, exploration 005) | Edit the cutoffs in config |
| Commercial line | Residential means residential-grade equipment: houses, apartments, co-op and condo units and whole apartment buildings, including a super or building manager calling for the building; commercial means a business with commercial-grade equipment, such as an office, store or restaurant (decided, eval round 2; was "three or more units is commercial", call journeys round 2) | Edit the rule in config |
| FSM | ServiceTitan, emulated by our mock with ServiceTitan's own field names (SOURCE: its OpenAPI documents, exploration 006; what the mock adds is listed in servicetitan_mock/README.md) | Point the adapter at the real API; agent code doesn't change |
| Texting | Not required by the assignment; none in V1 (DECISIONS.md 007) | Register a business texting brand and switch confirmations on |
| Language | English for V1; other languages later (docs/later.md) | Add Spanish through the same stack; evals add a Spanish slice |
