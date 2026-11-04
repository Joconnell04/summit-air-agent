# 015. Address lookup: finding the ZIP from the street the caller said

Researched against the live services from the build Mac, with no key and no cost. Jackson's idea, built in this lane and proposed for decision in the PR. Numbers are SOURCE, MEASURED or ASSUMED.

## Recommendation

Use NYC Planning's GeoSearch (`geosearch.planninglabs.nyc`) first and the US Census geocoder only when GeoSearch fails. GeoSearch is built on the city's own Property Address Directory, so it reads the addresses New Yorkers actually say: Queens hyphenated house numbers, street aliases and no borough at all. The Census geocoder needs a city or state with every query and misses some Queens addresses, which makes it a fallback, not a first choice. Both are free and need no key.

Accept a result only when the house number and the street are the same as the caller's. Both services are fuzzy search engines and return near matches; GeoSearch marks even an exact match `match_type: fallback` with confidence 0.8 (MEASURED, every query below), so its own score can't be the test.

## What was tried

| Query | GeoSearch | Census |
|---|---|---|
| `248 duffield st` | 248 Duffield Street, 11201, first result; 89 ms | no match without a city |
| `248 duffield st, brooklyn ny` | same | 248 Duffield St, 11201; 157 ms |
| `248 duffield st, ny` | same | 248 Duffield St, 11201 |
| `41-12 bell blvd` (no such house) | near matches 41-00 and 42-41 Bell Blvd | no match |
| `42-41 bell blvd, queens, ny` | 42-41 Bell Boulevard, 11361 | no match with "Queens"; found with "Bayside" |
| `100 broadway` | Brooklyn 11249 and Manhattan 10005, both exact | 44 matches across the state with "ny" |
| `1 main st` | Brooklyn 11201 (DUMBO) and Manhattan 10044 (Roosevelt Island) | |
| `77-10 34 ave` | 77-10 34th Avenue, 11372 | |
| `3520 82nd st` | 35-20 82nd Street, 11372 (written 3520) | |

Times are MEASURED, one request each from the build Mac; GeoSearch answered in 77 to 103 ms over six queries.

## How it is built

- `app/geocode.py`: the two backends, the exact-match rule, and a cache by house and street. A lookup starts as soon as a caller's line has a house number and a street (digits or digits said one by one), so the answer is usually back before the agent asks.
- `find_address` (docs/tools.md): one place fills the ZIP and the read-back confirms it; several ask which borough or neighborhood; none asks the caller to spell the street, then for the ZIP; down or slow asks for the ZIP.
- The ZIP gate (`tools._zip_known`) counts a ZIP from a lookup only when the house number and street came from the caller's own lines, so the model can't make up a street to get a ZIP.
- A ZIP the caller gave that disagrees with the one lookup match is read back against the address found, and the server books on neither until the caller says theirs again or picks the lookup's. An address on the caller's record keeps its ZIP: the company has been there.
- City names and the service URL live in `tenants/summit-air/config.yaml` (`address_lookup`), so the module names no city.
- Off unless `GEOCODER=geosearch` is set on the server, as the weather and the side model are.

## In the evals

The eval world never calls out. It answers from `evals/addresses.yaml`, real GeoSearch results recorded with `uv run python -m evals.addresses "<house> <street>"`, and any other address is answered as an outage, so the agent asks for the ZIP as before. That matters because many older scenarios use made-up pairs: 620 Bedford Ave is 11249 in the city's directory, not the seed's 11235, and of the 73 scenario addresses with a house number, 25 have no exact house in it and 12 more sit in a different ZIP from the one the scenario gives (MEASURED, a run over every committed scenario's `hidden_facts.address`). A live lookup would question those calls for reasons that have nothing to do with what they test.

## Open questions

- Timeouts are ASSUMED: 1.5 s a backend, 3 s for `find_address` to wait, 0.5 s for the read-back and booking checks. Tune them from live calls (docs/tuning.md).
- GeoSearch is a city service with no published rate limit or uptime figure; the Census fallback and the ask-for-ZIP fallback cover an outage, but a paid geocoder would be the change if it proves unreliable at volume.
- The Census answer has no neighborhood, so a question built from two Census matches names the borough or the ZIP.

## Sources

- GeoSearch, NYC Planning Labs: https://geosearch.planninglabs.nyc (v2 search, Pelias over the Property Address Directory; the `addendum.pad` field in each answer names the PAD version).
- US Census Bureau geocoder, one-line address endpoint: https://geocoding.geo.census.gov/geocoder/ (benchmark `Public_AR_Current`).
