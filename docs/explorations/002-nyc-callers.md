# 002. Who actually calls an NYC residential HVAC company

Issue #12. Researched 2026-11-02. Free tools only (WebSearch, WebFetch, Census Reporter API); no paid calls. Numbers are SOURCE (linked in section 6) or ASSUMED.

## 1. Question

Who calls a residential HVAC shop in NYC and nearby counties, what are they living in and heating with, what do NYC rules and utility guidance say that the agent must match, who is at risk, which languages do they speak, and what else do they ask for beyond repair and maintenance?

## 2. Why it matters to Summit Air

`docs/users.md` caller 2 (vulnerable household), 3 (safety), 6 (renter) and the traits list (language, C-16) are written from general knowledge. `docs/brief.md` and `docs/spec.md` require a safety script before any question (C-12), priority that only rises (C-09), and a residential-only gate (C-05). Each needs real NYC facts: who is a tenant, which utility to name, which phrases signal risk. The kickoff says "three counties" without naming them (`docs/assignment.md`).

## 3. Options: implications for users.md and the agent

Background numbers first. All SOURCE: ACS 2024 1-year (B25003, B25024, B25040, B16001, computed by us from Census Reporter); LEP = speaks English less than "very well", share of residents 5+.

| Area | Owner-occupied | Units in 1 or 2 family | Utility gas / oil heat | LEP | Top LEP languages |
|---|---|---|---|---|---|
| Queens | 44.4% | 46.4% | 74.7% / 8.4% | 29.6% | Spanish 12.4, Chinese 6.9, Bengali 2.2, Korean 1.1 |
| Brooklyn | 29.1% | 27.9% | 72.4% / 5.2% | 24.4% | Spanish 7.4, Chinese 4.9, Russian 3.2, Yiddish 2.4 |
| Staten Island | 67.7% | 82.1% | 87.0% / 2.9% | not retrieved | not retrieved |
| Nassau | 81.3% | 83.8% | 64.0% / 24.9% | 13.5% | Spanish 6.8, Chinese 1.9, Haitian 0.8 |
| Westchester | 61.3% | 56.6% | 53.8% / 23.8% | 14.8% | Spanish 10.9, Chinese 0.5 |

Also SOURCE (NYCHVS 2023): citywide 68% renter; owner-occupied share is 81% of single-family homes, 48% of two-unit buildings, 19% of three-to-five-unit buildings, 69% of co-op units, 40% of condo units. 12% of NYC units reported a heating breakdown the prior winter. Air conditioning: 90.9% of Brooklyn, 92.5% of Queens, 96.6% of Staten Island households have some unit (Census experimental estimate, via NY Comptroller). Not found: boiler vs furnace vs steam shares, and central vs window AC shares. ASSUMED: older houses and two-families lean to gas or oil boilers with radiators (hot water or steam); Nassau, Westchester and Staten Island detached houses lean to forced air; window and through-wall units dominate apartments, ductless and central dominate houses. ASSUMED counties: three of Kings, Queens, Richmond, Nassau (a residential-only shop with 40 techs fits 1 to 2 family housing). Manhattan and the Bronx are mostly large buildings, so unlikely.

**A. Ownership is a real branch, not an edge case.** What callers say: "I rent the upstairs", "my landlord is the one downstairs", "we're in a co-op". About half of two-unit homes and 19% of single-family homes are rented, and 60% of condo units. NYC Admin Code 27-2028 covers "every tenant-occupied one or two-family dwelling" (code mirror; SOURCE). Heat season is Oct 1 to May 31: at least 68F from 6am to 10pm when outside is below 55F, at least 62F from 10pm to 6am regardless, hot water 120F all year; tenant contacts the landlord first, then 311 (HPD, SOURCE). No NYC cooling duty on landlords was found. What the agent must ask: own or rent, and who pays, on every no-heat call in season. Record the authorizer apart from the caller (C-17). Do not give legal advice; a single neutral line ("NYC requires owners to provide heat; 311 takes complaints") is Jackson's call. These rules cover the five boroughs only; Nassau and Westchester tenants are not covered by them (ASSUMED, not researched). Effort: low (prompt, one field, tests). Risk: sounding like legal advice, or booking work the tenant cannot approve.

**B. Gas and carbon monoxide are two scripts, and neither keeps the caller on the phone.** Con Edison: "Leave immediately and take others with you... Call 911 or 1-800-752-6633", and do not "touch appliances or electronics, including your phone." National Grid: "go outside and call 911 or 1-718-643-4050 immediately" (Brooklyn, Queens, Staten Island); Long Island and Rockaways 1-800-490-0045. NYC Health for a CO alarm: "Open your windows. Leave your home. Get to fresh air immediately. Call 911," then Poison Control 212-764-7667. Con Edison: a CO detector is not a gas detector. National Grid: odorant can fade, so no smell does not rule out a leak. What the agent must do: say the script first, tell the caller to hang up and go outside, never ask booking questions, and flag a person at once. Gas says leave without touching anything; CO says open windows first. Name 911 always; add the utility number by service address from config. HPD requires CO detectors in multiple dwellings and non-owner-occupied 1 to 2 family homes with fossil fuel; owner-occupied houses fall under NY State's Amanda's Law (snippet only). Effort: low. Risk: wording needs sign-off by a person (C-12).

**C. Vulnerable callers describe a person, not a diagnosis.** SOURCE (DOHMH 2025): about 5 heat-stress and about 520 heat-exacerbated deaths a year (2018 to 2022); highest rates at 60+; 89% had a chronic condition; of 15 home deaths with known AC status, none had or used AC. Cold: NYC and NYS materials name older adults and infants, who can suffer hypothermia indoors (2013 NYS release, snippet). Phrases (ASSUMED, to become eval lines): "my mother is 85", "he's on oxygen", "the baby is three weeks old", "she's on dialysis", "she can't get up", "he has dementia", "she's alone and not answering", "heart condition", "chemo". What the agent must ask: who is in the home and their age or condition, on every no-heat or no-AC call, and where the person is right now. Programs exist but the agent should not promise them: Con Edison life-sustaining equipment registration (respirators, dialysis, apnea monitors; 1-800-752-6633; medical certification within 60 days); cooling centers open at heat index 95F for two days or 100F for one (DOHMH 2026); HEAP cooling closed June 5, 2026. If oxygen is unavailable, NYC EM says call 911. A separate Con Edison medical-hardship shutoff program was not verified. Effort: medium (classifier lines, evals).

**D. Spanish first, then a handoff path; and a request mix beyond repair.** LL30 top ten (SOURCE): Spanish, Chinese, Russian, Bengali, Haitian Creole, Korean, Arabic, Urdu, French, Polish. Citywide 48.3% speak another language at home and 23.3% are LEP (ACS 2024). Spanish is the top LEP language in every county checked; Chinese second in Queens, Brooklyn and Nassau; Russian and Yiddish matter in Brooklyn. LEP is measured across all residents, so callers who own homes are probably less LEP (ASSUMED). Agent: detect language, serve Spanish, and for others capture language and number and promise a callback in that language (C-16). Beyond repair, expect: install quotes and heat pump rebates (NYS Clean Heat 2026 to 2030, 1 to 4 unit homes, participating contractors only; Con Edison cold-climate heat pump rate $2,500, or $4,500 in a disadvantaged community, per the March 2026 manual summary; federal 25C credit ended Dec 31 2025, secondary source); oil-to-gas conversion (National Grid territory, needs gas on the street); boiler "inspections" (DOB annual inspection excludes buildings of 5 families or fewer, so a house owner asking is asking for a voluntary tune-up; a 6+ unit building is commercial for us); Local Law 97 (buildings over 25,000 sq ft; not houses); window units and duct cleaning (not researched). Agent: take installs as a lead with no price or rebate promise (C-21).

## 4. Recommendation, and what would change it

Fold A to D into users.md through `/round`, in this order: B (safety wording, highest stakes), C (phrase list into evals), A (ownership ask), D (Spanish plus handoff). Add the county table to `docs/brief.md` as an assumption. Keep county, ZIP and utility numbers in config so the agent works for any three counties.

What would change it: counties turn out to be Nassau and Westchester (owner share 61 to 81%, oil heat about 24%, no NYC heat law, so A shrinks and oil-burner calls grow); Summit Air is not a Clean Heat participating contractor (drop rebate talk); the voice stack lacks Spanish (handoff only); Paul says tenants are out of scope.

## 5. Open questions for Jackson

1. Which three counties? Kings, Queens, Richmond, or Nassau in place of one?
2. Should the agent say the one-line heat-law note to tenants, or only book and stay silent on law?
3. Spanish live in v1, or handoff only for every non-English caller?
4. Does Summit Air sell installs and heat pumps, and is it a Clean Heat participating contractor?
5. Who signs off the gas and CO wording, and is a callback after the caller reaches safety wanted?

## 6. Sources

Read in full unless marked snippet. Accessed 2026-11-02; most pages are undated.
- HPD heat and hot water: https://www.nyc.gov/site/hpd/services-and-information/heat-and-hot-water-information.page
- Admin Code Art. 8 (mirror; its night rule is outdated, HPD page governs): https://nycadmincode.readthedocs.io/t27/c02/sch02/art08/index.html
- Con Edison gas safety: https://www.coned.com/en/safety/energy-safety/gas-safety
- National Grid NYC gas emergency: https://www.nationalgridus.com/NY-Home/Natural-Gas-Safety/Report-a-Gas-Emergency
- National Grid Long Island (snippet): https://www.nationalgridus.com/Long-Island-NY-Home/Natural-Gas-Safety/Report-a-Gas-Emergency
- NYC Health carbon monoxide: https://www.nyc.gov/site/doh/health/health-topics/carbon-monoxide.page
- HPD detectors: https://www.nyc.gov/site/hpd/services-and-information/detectors.page
- NYC EM utility disruptions (Con Edison and National Grid areas, life-sustaining equipment): https://www.nyc.gov/site/em/ready/utility-disruptions.page
- DOHMH 2025 heat mortality report: https://a816-dohbesp.nyc.gov/IndicatorPublic/data-features/heat-report-archive/2025/
- DOHMH 2026 release (cooling centers, HEAP): https://www.nyc.gov/site/doh/about/press/pr2026/nyc-health-department-releases-report-on-heat-related-deaths.page
- NYCHVS 2023 initial findings (Table 2, heating breakdown): https://www.nyc.gov/assets/hpd/downloads/pdfs/about/2023-nychvs-selected-initial-findings.pdf
- ACS 2024 1-year via Census Reporter (the Census API itself needs a key): https://api.censusreporter.org/1.0/data/show/latest?table_ids=B16001&geo_ids=05000US36081 (swap table and geo ids)
- NY Comptroller, air conditioning availability (Census LACE, 2023, experimental): https://www.osc.ny.gov/reports/air-conditioning-availability-new-york
- Local Law 30 languages (MOIA FY2023 report): https://www.nyc.gov/assets/immigrants/downloads/pdf/FY2023-local-law-30-reports.pdf
- DOB boiler compliance: https://www.nyc.gov/site/buildings/safety/boiler-compliance.page
- Local Law 97 thresholds (snippet): https://www.nyc.gov/site/buildings/codes/ll97-greenhouse-gas-emissions-reductions.page
- NYS Clean Heat manual, March 2026 (snippet summary, amounts unverified in full): https://cleanheat.ny.gov/assets/pdf/NYS%20Clean%20Heat%20Program%20Manual%202025_v2.pdf
- NYS Amanda's Law (snippet): https://Health.NY.Gov/environmental/emergency/weather/docs/co_amandas_law.pdf
- HPD heat season alert 2024 (Class C, 24 hours; snippet): https://www.nyc.gov/site/hpd/news/029-24/heat-season-alert-city-mobilizes-keep-new-yorkers-safe-this-winter

Gaps: Staten Island language data; boiler, furnace and central AC shares; Westchester gas utility; Nassau and Westchester tenant heat rules; Con Edison medical-hardship shutoff program; duct cleaning and window-unit demand.
