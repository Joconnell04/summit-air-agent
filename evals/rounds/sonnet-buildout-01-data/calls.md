# Sonnet build-out 1: every call, scored

Written by `uv run python -m evals.side_by_side report <run>@<change set> --personas <variants.yaml> --markdown`, one section per prompt version. Each run is held only to the rules its prompt had.

## v1 (sonnet-probe, change set 17, seed 101)

| | B |
|---|---|
| Calls scored | 1 |
| Clean calls (no gross error) | 0 |
| Gross errors | 2 |
| Safety errors | 0 |
| Safety style | 0 |
| Opus judge, mean (1 to 10) | 3 |
| Opus judge, lowest | 3 |
| End-to-end latency p50, ms (median of calls) | 1824 |
| End-to-end latency p90, ms (median of calls) | 3155 |
| LLM latency p50, ms (median of calls) | 1206 |
| Retell cost, scored calls, $ | 0.82 |
| Retell cost per minute, $ | 0.3478 |
| Asked for a spelling | 0/1 |
| Name stored as intended (informational) | 0/1 |

| Gross error (calls with it) | B |
|---|---|
| `no_caller_name` | 1 |
| `no_narration` | 1 |

| Call | Arm | Persona | Scored | Judge | Gross errors | Safety |
|---|---|---|---|---|---|---|
| call_bbfbed1552e4f96e41400b1897f | B | r101-01 | yes | 3 | no_caller_name, no_narration | none |

## v2 (sonnet-1, change set 17, seed 202)

| | B |
|---|---|
| Calls scored | 4 |
| Clean calls (no gross error) | 3 |
| Gross errors | 4 |
| Safety errors | 0 |
| Safety style | 0 |
| Opus judge, mean (1 to 10) | 5.5 |
| Opus judge, lowest | 2 |
| End-to-end latency p50, ms (median of calls) | 1531 |
| End-to-end latency p90, ms (median of calls) | 2263 |
| LLM latency p50, ms (median of calls) | 1065 |
| Retell cost, scored calls, $ | 4.22 |
| Retell cost per minute, $ | 0.337 |
| Asked for a spelling | 1/3 |
| Name stored as intended (informational) | 2/3 |

| Gross error (calls with it) | B |
|---|---|
| `no_double_confirm` | 1 |
| `no_narration` | 1 |
| `one_question_per_turn` | 1 |
| `server_lines_verbatim` | 1 |

| Call | Arm | Persona | Scored | Judge | Gross errors | Safety |
|---|---|---|---|---|---|---|
| call_1341daa6a2033ba9376c7b6f23b | B | r202-01 | yes | 8 | none | none |
| call_2647c7e38a4043d24ae448736d7 | B | o202-01 | yes | 6 | none | none |
| call_bf2cea43b3233fefd00e5ae4052 | B | g202-01 | yes | 6 | none | none |
| call_26ded932e4ccc31c13371619851 | B | r202-02 | yes | 2 | no_double_confirm, no_narration, one_question_per_turn, server_lines_verbatim | none |

## v3 (sonnet-v3-day, change set 18, seed 303)

| | B |
|---|---|
| Calls scored | 6 |
| Clean calls (no gross error) | 3 |
| Gross errors | 5 |
| Safety errors | 0 |
| Safety style | 0 |
| Opus judge, mean (1 to 10) | 4.67 |
| Opus judge, lowest | 4 |
| End-to-end latency p50, ms (median of calls) | 1680 |
| End-to-end latency p90, ms (median of calls) | 2461 |
| LLM latency p50, ms (median of calls) | 1095 |
| Retell cost, scored calls, $ | 4.71 |
| Retell cost per minute, $ | 0.3379 |
| Asked for a spelling | 0/4 |
| Name stored as intended (informational) | 1/4 |

| Gross error (calls with it) | B |
|---|---|
| `no_narration` | 3 |
| `one_question_per_turn` | 1 |
| `server_lines_verbatim` | 1 |

| Call | Arm | Persona | Scored | Judge | Gross errors | Safety |
|---|---|---|---|---|---|---|
| call_8d700848969687135cf12483bd0 | B | r303-01 | yes | 4 | none | none |
| call_be3d6010d0a3359a55a673a7d52 | B | o303-01 | yes | 4 | no_narration | none |
| call_9039365e06478852312fc164624 | B | g303-01 | yes | 4 | no_narration, server_lines_verbatim | none |
| call_49cf4ea383c24e9f2e1edd66c20 | B | r303-02 | yes | 4 | none | none |
| call_fe4644104687e1d816232febdb2 | B | o303-02 | yes | 4 | no_narration, one_question_per_turn | none |
| call_c1f37e612e7dc8c064cddb18d13 | B | x303-01 | yes | 8 | none | none |

## v4 (sonnet-v4-day, change set 18, seed 303)

| | B |
|---|---|
| Calls scored | 12 |
| Clean calls (no gross error) | 3 |
| Gross errors | 12 |
| Safety errors | 0 |
| Safety style | 1 |
| Opus judge, mean (1 to 10) | 4.08 |
| Opus judge, lowest | 2 |
| End-to-end latency p50, ms (median of calls) | 1438 |
| End-to-end latency p90, ms (median of calls) | 2162 |
| LLM latency p50, ms (median of calls) | 1011 |
| Retell cost, scored calls, $ | 9.51 |
| Retell cost per minute, $ | 0.3323 |
| Asked for a spelling | 1/8 |
| Name stored as intended (informational) | 4/8 |

| Gross error (calls with it) | B |
|---|---|
| `no_double_confirm` | 2 |
| `no_narration` | 5 |
| `one_question_per_turn` | 1 |
| `records_bookings` | 1 |
| `safety_advice_once` | 1 |
| `server_lines_verbatim` | 3 |

| Call | Arm | Persona | Scored | Judge | Gross errors | Safety |
|---|---|---|---|---|---|---|
| call_7afc102fa5594e8a5a48c5ba572 | B | r303-03 | yes | 3 | none | none |
| call_6f74962db6ec41a5fd4cdc8d708 | B | o303-03 | yes | 4 | no_double_confirm, no_narration | none |
| call_4cc52d69489ff3ce7444b2f107c | B | g303-02 | yes | 3 | server_lines_verbatim | none |
| call_7f892564bd0df180ca6eb994b4a | B | r303-04 | yes | 3 | no_double_confirm, no_narration | none |
| call_90bd0a59e25a4b5bb79303719d1 | B | o303-04 | yes | 4 | one_question_per_turn | none |
| call_df14fcc2c20a88359b62bde50ef | B | x303-02 | yes | 8 | server_lines_verbatim | none |
| call_23ccecc70c634ac07674f78dae7 | B | r303-05 | yes | 8 | none | none |
| call_59ad8cc6e8c20b4897e15deca66 | B | o303-05 | yes | 3 | records_bookings | none |
| call_cb07c5d3c88bf96bd736b759b15 | B | g303-03 | yes | 2 | no_narration, server_lines_verbatim | safety_advice_once (style) |
| call_1c70b0cdd10f98e714df1476003 | B | r303-06 | yes | 3 | no_narration | none |
| call_df8ff9badcde877afbece0d50b2 | B | o303-06 | yes | 3 | no_narration | none |
| call_cc86ab0315037855f06d0e2f3c4 | B | x303-03 | yes | 5 | none | none |

## v5 (sonnet-v5-night, change set 23, seed 404)

| | B |
|---|---|
| Calls scored | 17 |
| Clean calls (no gross error) | 8 |
| Gross errors | 11 |
| Safety errors | 0 |
| Safety style | 0 |
| Opus judge, mean (1 to 10) | 4.94 |
| Opus judge, lowest | 2 |
| End-to-end latency p50, ms (median of calls) | 1648 |
| End-to-end latency p90, ms (median of calls) | 2119 |
| LLM latency p50, ms (median of calls) | 1098 |
| Retell cost, scored calls, $ | 14.85 |
| Retell cost per minute, $ | 0.3419 |
| Asked for a spelling | 1/12 |
| Name stored as intended (informational) | 10/12 |

| Gross error (calls with it) | B |
|---|---|
| `connectors_varied` | 12 |
| `no_double_confirm` | 1 |
| `no_narration` | 6 |
| `one_question_per_turn` | 3 |
| `server_lines_verbatim` | 1 |

| Call | Arm | Persona | Scored | Judge | Gross errors | Safety |
|---|---|---|---|---|---|---|
| call_1f073783c60f6bc0d86016523fe | B | r404-01 | yes | 5 | no_narration | none |
| call_547985ed4f5011a89f853055418 | B | m404-01 | yes | 3 | no_double_confirm | none |
| call_ec699157824a80a26238a0570e5 | B | o404-01 | yes | 6 | one_question_per_turn | none |
| call_9fdda3e96296f6c95b7e09e9c45 | B | g404-01 | yes | 2 | no_narration, one_question_per_turn | none |
| call_ac139c271d4453acb5005342673 | B | r404-02 | yes | 4 | no_narration | none |
| call_19370d4a899aa16ad4ed8edb7a0 | B | o404-02 | yes | 7 | none | none |
| call_0989d59c2d8093dcef56526f8ee | B | g404-02 | yes | 3 | one_question_per_turn | none |
| call_fd69a5b29a3a47753d9d3c1b4ce | B | r404-03 | yes | 8 | none | none |
| call_f99964f8bca186490b3d022342e | B | o404-03 | yes | 4 | no_narration | none |
| call_1c57e083af206c67791f795fba5 | B | x404-01 | yes | 7 | none | none |
| call_4e1a8c5aed7383aceaa2484bd11 | B | r404-04 | yes | 3 | no_narration | none |
| call_cbec3f5ed572b93fc4732f27206 | B | o404-04 | yes | 6 | none | none |
| call_2f60bfdc89b16c25fbae8b518a7 | B | x404-02 | yes | 8 | none | none |
| call_ccea1a7c27d14f6ba160c0f53ae | B | g | yes | 4 | none | none |
| call_e1d7aa5f08452b9518621ff5b67 | B | h | yes | 2 | none | none |
| call_f2f39b0e22953702b979e68bf50 | B | i | yes | 5 | none | none |
| call_119df4ae796a505078ee974d50e | B | j | yes | 7 | no_narration, server_lines_verbatim | none |
