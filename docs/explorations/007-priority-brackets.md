# 007. Priority brackets for no heat and no cooling

Issue: none opened yet. Researched 2026-11-02 with WebSearch, WebFetch, curl and pdftotext; no paid calls. Numbers are SOURCE (linked in section 6), MEASURED (my own request on the date above) or ASSUMED. No source gives a dispatch cutoff for a residential HVAC shop; every cutoff below is a judgment placed inside a sourced band, and says so.

## 1. Question

What inside-temperature brackets, and what use of outside temperature, should set priority for no heat and no cooling? How can the server read NYC's outside temperature for free? Which household situations beyond "elderly" should raise priority, in the words a caller would use?

## 2. Why it matters to Summit Air

The assignment's one named example is no heat in January with an elderly person at home, to be flagged "immediately" (`docs/assignment.md`). Today `tenants/summit-air/agent.yaml` starts both job types at High and has one cutoff each (below 55 F, above 85 F) plus a short vulnerable list (elderly, baby, age 70 or more, equipment). That sends every cold-snap and heat-wave call to the same level, and Urgent slots are held back (S-25), so over-flagging costs real capacity. `docs/spec.md` S-02 and S-21 and considerations C-09 already call for the inside temperature, a phrase floor and live outdoor temperature. Journey 11's after-hours cutoffs (50, 55, 80, 85 F, ASSUMED) are a second set of numbers and should read from the same table.

## 3. Options

**What the sources say.** Cold: NYC heat law needs 68 F by day when outside is below 55 F and 62 F at night regardless, Oct 1 to May 31 (SOURCE, HPD). WHO proposes 18 C (64.4 F) as a safe minimum for temperate climates, with a higher one for older people, children and chronic illness (SOURCE, WHO 2018 via NCBI snippet; Public Health Wales 2022 repeats it). NIA says 60 to 65 F can cause hypothermia in older adults (SOURCE, snippet). CDC says infants under one should never sleep in a cold room and gives no indoor number (SOURCE). Pipes can freeze below 55 F indoors (SOURCE, contractor; Red Cross says no lower than 55 F when away, snippet). ASHRAE 55 is a comfort standard with no health limit (the current edition's ranges were not retrievable), so it is not a priority cutoff. Maintenance software Latchel defaults emergencies to 45 F (95 F hot) for ordinary households and 55 F (85 F hot) for infants, elderly and disabled residents (SOURCE, vendor). Revin's blog says tonight below 50 F or falling fast (SOURCE, vendor, via 005).
Heat: NYC has no landlord cooling duty today. Local Law 23 of 2026 defines adequate cooling as 78 F, Jun 15 to Sep 15, but owner duties start June 1, 2030 (SOURCE, primary text). Health Canada (June 2026) sets 26 C (78.8 F) as the upper indoor limit for people 60 and over and calls above 31 C (87.8 F) a critical threshold (SOURCE). NYC cooling centers open at a heat index of 95 F for two days or 100 F for one (SOURCE, 002). NYC's mortality report says non-extreme hot days cause more heat deaths than extreme days, and indoor heat runs above outdoor at night (SOURCE).

**A. Inside-temperature brackets only.** A list of brackets in the tenant config replaces `base` plus the two single-cutoff rules; `app/priority.py` picks the starting level from the caller-reported temperature, and the existing rules still only raise it. Effort: low (about 40 lines, bracket tests; ASSUMED). Cost: none. Risk: a warm-day no-heat call cannot drop to Normal; callers often have no thermometer.

**B. A plus outside temperature (Jackson's idea).** Adds a small weather reader. Effort: medium (one module, a background refresh job, fallbacks, fixture tests; ASSUMED half a day). Cost: none. Risk: a stale or missing read. Mitigation: an unknown outside temperature never lowers a call, and Normal needs a warm outside reading and an inside reading of 65 F or more, so the model alone cannot talk a call down.

Proposed table, no heat (level is the starting level; later rules only raise it):

| Inside (caller) | Outside now or tonight's low (server) | Starts at | Basis |
|---|---|---|---|
| below 55 F | any | Urgent | 55 F pipes and Latchel vulnerable default; the repo's current line. Use 50 if Urgent slots run short (Revin 50, Latchel 45) |
| 55 to below 65 F | any | High | 62 F HPD night floor; 64.4 F WHO; NIA 60 to 65 F |
| 65 F or more, or not given | below 55 F | High | HPD's own 55 F outside line; the house keeps falling (ASSUMED) |
| 65 F or more | 55 F or more, tonight's low too | Normal | an unheated home drifts toward outside, so it cannot reach 55 F (ASSUMED physics) |
| not given | unknown | High | fail safe |

Why not the 60s or 70s as a High ceiling: 68 to 74 F is comfort and the tenant's legal daytime floor, a landlord matter, not a health line. I found nothing supporting a bump above about 65 F. Season (Oct 1 to May 31) adds nothing when outside temperature is known; use it only as the fallback when the weather read fails (in season means treat as cold).

Proposed table, no cooling:

| Inside (caller) | Outside | Starts at | Basis |
|---|---|---|---|
| above 85 F | any | Urgent | Health Canada critical 87.8 F, rounded down; Latchel 85 F; the repo's current line |
| 79 to 85 F | any | High | 78 F LL23; 78.8 F Health Canada |
| below 79 F or not given | 80 F or more, or an NWS heat advisory active | High | 80 F is ASSUMED; mortality is mostly on non-extreme days, so do not wait for an advisory |
| below 79 F | below 80 F | Normal | ASSUMED |
| not given | unknown | High | fail safe |

**The outside read.** National Weather Service, no key. SOURCE: a User-Agent naming the app and a contact is required, the rate limit is unpublished ("generous"; a throttled call can retry in about 5 s). MEASURED 2026-11-02: ten back-to-back reads all returned 200 in 0.07 to 0.18 s with no throttling; the endpoint answered without a User-Agent too, but send one anyway.

- Now: `GET https://api.weather.gov/stations/KNYC/observations/latest` with headers `User-Agent: summit-air-agent (ops@summit-air.example)` and `Accept: application/geo+json`. Read `properties.temperature.value` (degrees C, can be null; F = C x 9/5 + 32). MEASURED: 200 in 0.25 s, `s-maxage=300`.
- Tonight's low: `GET https://api.weather.gov/gridpoints/OKX/33,42/forecast/hourly?units=us` (grid from `/points/40.7128,-74.0060`, MEASURED); take the minimum of the next 12 periods. MEASURED: 200 in 0.39 s, 164 KB, `s-maxage=3600`.
- Alerts: `GET https://api.weather.gov/alerts/active?point=40.7128,-74.0060`, match event names containing "Heat" or "Cold". MEASURED: 200 in 0.17 s, `s-maxage=5`.

Refresh all three hourly in a background job (about 72 requests a day, ASSUMED), keep the last good value up to 3 hours, and never call out during a caller's turn (latency budget). Fallbacks: KLGA or KJFK, then the first forecast period, then Open-Meteo (keyless, but the free tier is non-commercial only, under 10,000 calls a day; SOURCE), then "unknown". One Central Park point can differ from Staten Island or Nassau by a few degrees (ASSUMED), which the 55 F and 80 F margins absorb.

**C. B plus a broader vulnerability model (recommended).** Phrase groups in `agent.yaml` replace the single `vulnerable_person` rule; each group has a level, and two distinct groups together make Urgent (NYC Health's cold alert lists risk as no heat AND one of: age 60 or more, mental illness or developmental disability, heart, lung or diabetes, isolated or limited mobility; SOURCE, Feb 2026). Effort: medium (yaml, a `stack` count in `decide`, eval lines for each false alarm as `docs/users.md` requires). Risk: false alarms ("baby, it's cold", "my grandmother's house", "oxygen sensor"). Phrases are written as `words()` leaves them: lowercase, no apostrophes ("cant get up"). The older-adult row builds on the age patterns now in the working tree of `app/priority.py` ("she's 90", "in her 80s").

| Group | Typical caller words | Level | Basis |
|---|---|---|---|
| Infant under 1 | baby, infant, newborn, "3 months old", "6 weeks old" | Urgent | CDC infants under one |
| Young child | toddler, "2 year old", little kids, preschooler | High | WHO review groups (under 5); level ASSUMED |
| Older adult | elderly, senior, grandma, grandpa, "in her eighties", age 65 or more | Urgent | CDC 65; NYC Health uses 60 (60 to 64 High) |
| Parent, no age given | "my mom", "my dad", "my mother" | High, then ask age | ASSUMED |
| Medical equipment | oxygen, concentrator, cpap, bipap, dialysis, nebulizer, ventilator, hospital bed | Urgent | existing rule, S-21 |
| Bedbound, hospice | bedridden, bedbound, "cant get up", "cant walk", hospice, home health aide | Urgent | Chicago 1995 heat study: confined to bed odds ratio 8.2 |
| Limited mobility, disability | wheelchair, walker, disabled, paralyzed, "special needs" | High | NYC Health: limited mobility |
| Alone or isolated | "lives alone", "by herself", "no one else" | High | same study, living alone 2.3 |
| Chronic illness | heart condition, copd, asthma, lung disease, diabetic, kidney disease | High | NYC Health heat and cold alerts |
| Cognitive or mental | dementia, alzheimers, confused, "memory problems" | High | NYC Health heat alert |
| Pregnancy | pregnant, expecting, "due in", third trimester | High | CDC heat risk; cold evidence observational only |
| Recent surgery, immune | "just had surgery", post op, chemo, transplant, immunocompromised | High | ASSUMED; no source found |
| Symptoms now | shivering, "cant stop shaking", dizzy, "short of breath", "feels faint" | Urgent | CDC hypothermia signs; breathing trouble still gets the 911 line |
| Pipes, no other source | "pipes froze", "frozen pipes", "no other heat", "no space heater", "only heat" | High | contractor 55 F pipe rule; rest ASSUMED |
| Improvised heating | "using the oven", "stove on for heat", kerosene heater, generator inside, grill inside | Urgent plus the CO check question | NYC Health: do not use an oven to heat; NYC EM CO warnings |

Left out on purpose: medications and substance use (callers will not say, and the agent should not ask). Burst pipes and flooding are a plumber's job; the agent declines kindly.

## 4. Recommendation, and what would change it

Do C: B's brackets and outside read, with the grouped phrase list. Build in this order: bracket table in config (A), vulnerability groups with evals, then the weather read, so the first two ship even if the third slips. Point journey 11's cutoffs at the same table. Keep every number in config marked ASSUMED, and tune the 55, 65, 79 and 80 F lines from the first 10 real calls.

What would change it: the owner wants fewer Urgent flags (move the cold line to 50 F); real callers rarely know their inside temperature (then outside plus the vulnerable groups carry the decision, and the inside brackets matter less); the NWS reads prove flaky in testing (go to option A and keep the fallback code out); a real customer launches (replace the User-Agent contact and move off Open-Meteo's free tier).

## 5. Open questions for Jackson

1. A warm-day no-heat call would start at Normal. That is a starting level, not a rule lowering one, but S-21 and C-09 say "can only raise". OK to word it that way through `/round`?
2. Urgent line for no heat: keep 55 F, or 50 F? What should High mean for booking (same day, or next day)?
3. Age line: 65 (CDC), 60 (NYC Health) or the current 70?
4. A vulnerable household with no heat on a warm day (outside 55 F or more, inside 65 F or more): keep Urgent as today, drop to High, or drop to Normal? I would drop to High, but S-21 as written says urgent.
5. Extend the short fixed safety list (`docs/users.md`) with improvised heating (oven, generator inside) so it triggers the CO check?
6. The 80 F outside line for cooling is ASSUMED. Fine to tune after live calls?

## 6. Sources

Read in full unless marked snippet. Accessed 2026-11-02.
- HPD heat and hot water (cites data to 5/31/2026): https://www.nyc.gov/site/hpd/services-and-information/heat-and-hot-water-information.page
- NYC Local Law 23 of 2026 (passed Dec 18, 2025; returned unsigned Jan 20, 2026): https://www.nyc.gov/assets/buildings/local_laws/ll23of2026.pdf
- Health Canada, upper indoor temperature limits for older adults (June 2026): https://www.canada.ca/en/health-canada/services/publications/healthy-living/guidance-upper-indoor-temperature-limits-protecting-older-adults.html
- NYC Health Alert 2026 #3, prolonged cold (Feb 4, 2026): https://www.nyc.gov/assets/doh/downloads/pdf/han/alert/2026/han-alert-3-prolonged-cold.pdf
- NYC Health Alert 2026 #15, extreme heat (Jun 10, 2026): https://www.nyc.gov/assets/doh/downloads/pdf/han/alert/2026/han-alert-15-extreme-heat.pdf
- NYC heat mortality report 2025: https://a816-dohbesp.nyc.gov/IndicatorPublic/data-features/heat-report-archive/2025/
- NYC EM winter weather health safety: https://www.nyc.gov/site/em/ready/winter-weather-health-safety.page
- CDC indoor safety in winter (page dated Aug 25, 2026): https://www.cdc.gov/disasters/winter/duringstorm/indoorsafety.html
- CDC heat risk factors (June 25, 2024): https://www.cdc.gov/heat-health/risk-factors/index.html
- WHO Housing and Health Guidelines 2018, via NCBI (snippet; page behind a captcha): https://www.ncbi.nlm.nih.gov/books/NBK535294/
- Public Health Wales, home temperature (Nov 29, 2022): https://publichealthwales.nhs.wales/news/what-temperature-should-homes-be-heated-to-for-health/
- NIA hypothermia, 60 to 65 F (snippet; fetch refused): https://www.nia.nih.gov/news/hypothermia-cold-weather-hazard
- Semenza et al., Chicago 1995 heat deaths, NEJM 1996 (snippet): https://pubmed.ncbi.nlm.nih.gov/8649494/
- ASHRAE 55 summary (no numeric ranges): https://en.wikipedia.org/wiki/ASHRAE_55
- Latchel emergency temperature defaults (vendor): https://help.latchel.com/s/topic/0TO5e000000h9wqGAA/How-to-Add-Emergency-Temperature-Thresholds
- Standard Heating emergency guide (contractor; pipes below 55 F): https://www.standardheating.com/emergency-heating-guide/
- Fieldproxy HVAC triage blueprint (vendor; no heat below 32 F is critical): https://www.fieldproxy.ai/blueprints/hvac-emergency-triage-system
- ServiceTitan HVAC service calls (read; defines emergency calls, no numeric priority levels): https://www.servicetitan.com/blog/hvac-service-calls
- National Weather Service API documentation: https://www.weather.gov/documentation/services-web-api
- Open-Meteo terms: https://open-meteo.com/en/terms
- Earlier work: `docs/explorations/002-nyc-callers.md` (cooling centers, heat law), `docs/explorations/005-revin-blog.md` (vendor after-hours thresholds).

Gaps: no ServiceTitan priority guide with temperatures (it ships no default; priority names are tenant-defined); no source for an indoor cold limit for pregnancy, recent surgery or immunocompromised; a NYC Health page seen in a search summary gives a stale night minimum (55 F below 40 F outside), which HPD's current page contradicts, so HPD governs (as in 002); the 82 to 94 F death share appeared only in a search summary and is not used.
