# Side-by-side 1: every call, scored

Written by `uv run python -m evals.side_by_side report <runs> --markdown` from results.json. "Replaced" means a later valid rerun of the same arm and persona is the scored call; "invalid" means the scripted caller broke its role or read its script; "excluded" is listed with its reason in `evals/side-by-side/excluded.yaml`.

| | A | B | C | D |
|---|---|---|---|---|
| Calls scored | 8 | 8 | 9 | 9 |
| Clean calls (no gross error) | 1 | 3 | 1 | 1 |
| Gross errors | 15 | 8 | 34 | 19 |
| Safety errors | 0 | 0 | 0 | 0 |
| Safety style | 1 | 0 | 3 | 3 |
| Opus judge, mean (1 to 10) | 4.38 | 4.5 | 3.44 | 4.67 |
| Opus judge, lowest | 3 | 3 | 3 | 3 |
| End-to-end latency p50, ms (median of calls) | 1168 | 1552 | 1160 | 1154 |
| End-to-end latency p90, ms (median of calls) | 1701 | 1799 | 1741 | 1760 |
| LLM latency p50, ms (median of calls) | 612 | 1087 | 618 | 601 |
| Retell cost, scored calls, $ | 2.34 | 7.16 | 2.53 | 3.09 |
| Retell cost per minute, $ | 0.0929 | 0.328 | 0.0952 | 0.1052 |
| Asked for a spelling | 0/4 | 0/4 | 0/5 | 0/5 |
| Name stored as intended (informational) | 4/4 | 2/4 | 4/5 | 4/5 |

| Gross error (calls with it) | A | B | C | D |
|---|---|---|---|---|
| `callback_on_the_day_asked` | 1 | 0 | 1 | 1 |
| `no_caller_name` | 1 | 0 | 7 | 2 |
| `no_double_confirm` | 1 | 0 | 0 | 0 |
| `no_narration` | 0 | 2 | 5 | 1 |
| `no_own_safety_question` | 0 | 0 | 2 | 1 |
| `no_premature_hangup` | 0 | 0 | 4 | 1 |
| `one_question_per_turn` | 3 | 1 | 2 | 1 |
| `open_door_verbatim` | 0 | 0 | 0 | 2 |
| `open_door_with_first_offer` | 1 | 0 | 0 | 2 |
| `records_bookings` | 2 | 2 | 3 | 2 |
| `records_dnc_undone` | 0 | 0 | 2 | 0 |
| `records_priority_raised` | 1 | 0 | 2 | 1 |
| `safety_advice_once` | 1 | 0 | 1 | 2 |
| `server_lines_verbatim` | 5 | 3 | 8 | 6 |

| Call | Arm | Persona | Scored | Judge | Gross errors | Safety |
|---|---|---|---|---|---|---|
| call_2dc9e56b577b784f5c79c3a9b75 | A | e | no (replaced) | 8 | open_door_with_first_offer | none |
| call_8df5ad10287f0d52fa5a8f50a65 | A | b | no (replaced) | 5 | no_narration, no_premature_hangup, server_lines_verbatim | none |
| call_0009860347f1f375c280f704723 | C | e | no (replaced) | 3 | no_caller_name, no_premature_hangup, one_question_per_turn, records_dnc_undone, server_lines_verbatim | none |
| call_60a297b19f8a2e1121f6470795a | C | b | no (replaced) | 5 | no_premature_hangup, records_callback, records_dnc_undone | none |
| call_b68d500b116603c2cf6a0c3b657 | A | c | no (invalid) | 3 | no_narration | none |
| call_72ad6abac6f8033fd9813ead44b | C | c | yes | 5 | none | no_own_safety_question (style) |
| call_2c871637f07dc6f5b4cb39afc0b | A | g | yes | 4 | no_caller_name, one_question_per_turn | none |
| call_a3c8b2a6de25a6e93e4ab9cef13 | C | g | yes | 3 | no_caller_name, server_lines_verbatim | none |
| call_d17b0088affb2f6ea3971164e98 | A | h | yes | 4 | no_double_confirm, one_question_per_turn | none |
| call_648823e9423ded73156c791361c | C | h | yes | 3 | no_caller_name, no_narration, server_lines_verbatim | none |
| call_fa7e6a9195cd4e70b45e82a2499 | A | i | no (invalid) | 4 | callback_on_the_day_asked, no_caller_name, server_lines_verbatim | none |
| call_fc470f8ddd2d56cf55be68fc119 | C | i | no (invalid) | 4 | callback_on_the_day_asked, no_caller_name, no_narration, server_lines_verbatim | none |
| call_72009cbe16703210509127925b0 | A | j | yes | 5 | server_lines_verbatim | none |
| call_7fe13e690e57e56405611cad333 | C | j | yes | 4 | no_caller_name, no_narration, one_question_per_turn, server_lines_verbatim | none |
| call_8fd9ff40c7f8d9bc587cf5d189c | A | k | no (excluded) | 5 | records_bookings, server_lines_verbatim | none |
| call_6930525bbbd0de20271b03ad9a3 | C | k | yes | 3 | no_caller_name, no_premature_hangup, records_bookings, records_priority_raised, server_lines_verbatim | none |
| call_90b5daf59b6689139186dbfdeb7 | B | e | no (replaced) | 4 | no_narration, records_bookings, records_priority_raised, server_lines_verbatim | none |
| call_4f214d509e856dee37f402af81b | B | b | no (replaced) | 4 | no_narration, records_bookings | none |
| call_691ff016e13210be91684957b2c | B | c | no (replaced) | 6 | none | none |
| call_8bea915381ea96aed9f23f16021 | B | g | yes | 4 | none | none |
| call_b608687ce923e70a8318a6a6f0e | B | h | yes | 6 | none | none |
| call_818de352e42a0eb17bb7b9634c0 | B | i | no (invalid) | 2 | no_narration | none |
| call_bd5004917c3aea28f8fd0cb6b30 | B | j | no (invalid) | 5 | no_narration | none |
| call_2853e6d1d3c7bcede4e86d67fc6 | B | k | yes | 5 | server_lines_verbatim | none |
| call_b4c83f184b5cb64748332a2e3c3 | D | e | yes | 3 | no_premature_hangup, open_door_verbatim, open_door_with_first_offer, records_bookings, records_priority_raised, server_lines_verbatim | none |
| call_70c2695c53936f197dc37ba88be | D | b | yes | 6 | records_bookings | none |
| call_396d8572aecdfbbfac936b7fc48 | D | c | yes | 7 | server_lines_verbatim | none |
| call_a0c123864f67baf0d5cf5bb19ce | D | g | yes | 5 | one_question_per_turn, server_lines_verbatim | none |
| call_40dc142eaba78a8933e91e54263 | D | h | yes | 4 | no_caller_name, server_lines_verbatim | safety_advice_once (style) |
| call_5b998cf4ab209eb48c2ae5e9a0b | D | i | yes | 5 | callback_on_the_day_asked, server_lines_verbatim | none |
| call_e98ed261ed7da01c31cf096e3ef | D | j | yes | 3 | no_caller_name, server_lines_verbatim | none |
| call_11f8ca0e3ab110857663e60edcc | D | k | yes | 6 | no_narration, open_door_verbatim, open_door_with_first_offer | none |
| call_6fadca65bda8e685505803eaf13 | A | e | yes | 3 | one_question_per_turn, open_door_with_first_offer, records_bookings, records_priority_raised, server_lines_verbatim | none |
| call_aaefcdad80a6be32906d9f54dab | A | b | yes | 5 | records_bookings, server_lines_verbatim | none |
| call_3fc15029208308042bde5c46b59 | C | e | yes | 3 | no_caller_name, no_premature_hangup, records_bookings, records_dnc_undone, records_priority_raised, server_lines_verbatim | none |
| call_b8641369a8b7e72f85cfaecc267 | C | b | yes | 3 | no_narration, no_premature_hangup, one_question_per_turn, records_bookings, records_dnc_undone, server_lines_verbatim | none |
| call_3a8da3060376cd206693acab7d0 | A | c | yes | 6 | none | none |
| call_9e1bbb6310e006595123e5a0a21 | A | l | yes | 3 | server_lines_verbatim | safety_advice_once (style) |
| call_360c4fe0ec61e0339879858f61d | A | i | yes | 5 | callback_on_the_day_asked, server_lines_verbatim | none |
| call_daecc57ee95de4ca98a4dd9a6db | B | e | yes | 3 | no_narration, records_bookings, server_lines_verbatim | none |
| call_9bc643de3f76efe9946512b19f0 | B | b | yes | 3 | no_narration, records_bookings | none |
| call_4a774cd492380831262f312d132 | B | c | yes | 8 | none | none |
| call_0e2a9d1bf24a8ff39834d3942bb | B | l | yes | 4 | server_lines_verbatim | none |
| call_89c1c4a057c0914e567e962564b | B | i | yes | 3 | one_question_per_turn | none |
| call_2d43302fa7e8505865657a7fbd7 | D | l | yes | 3 | none | no_own_safety_question (style), safety_advice_once (style) |
| call_4cb456950965b24ad524776c01a | C | c | no (invalid) | 5 | none | none |
| call_c5966951b6ae51d1213942dc02e | C | l | yes | 3 | no_caller_name, no_narration, no_premature_hangup, server_lines_verbatim | no_own_safety_question (style), safety_advice_once (style) |
| call_c9bf44aee43e165c12837521d8c | C | i | yes | 4 | callback_on_the_day_asked, no_caller_name, no_narration, server_lines_verbatim | none |
