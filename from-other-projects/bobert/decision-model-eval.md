# Decision providers vs. the regex gates: eval

Bottom line: OpenAI Decisions is clearly good enough to ship for ideas A, B and C at the default thresholds. Laya (the open-weights 421M model, not fine-tuned) is not: it misses 6 of 10 emergencies at 0.8 and 3 of 10 stop requests. Both sizes of sample are small, so read the numbers as direction, not precision.

Provenance: every number below is SOURCE (measured here by `eval.py`, raw answers in `results.json`, derived numbers in `results_summary.json`) unless marked ASSUMED. Labels are mine (hand-labelled by the eval author), not a second annotator's.

## What was tested

| Question | Items | Positives | Real material | Written |
|---|---|---|---|---|
| agreement | 40 | 23 yes, 7 no, 10 unclear | 9 sim transcripts, 11 db `heard` lines | 20 |
| emergency | 30 | 10 | 4 sim, 1 db | 25 |
| stop_request | 30 | 10 | 4 sim, 2 db | 24 |
| wrong_person | 20 | 8 | 3 sim, 1 db | 16 |
| outcome | 20 | 4 per outcome | 5 sim, 1 db | 14 |

Each item's source is in `labels.json`. Limits on the "real" part: the stress-call transcript is not in the `calls` table (4 rows: one Retell tool-only transcript, three LiveKit audio tests), so it was not used. The db `heard` lines are the caller's line only; where an agent question was not stored, the question text is written and the source says so. The sim callers are model-written, so "real" here means simulated-call text, not human callers. Several "emergency/stop/wrong_person" negatives are sim lines that happen to be unrelated.

Requests mirror `app/decide.py`: agreement is `Agent: ...\nCaller: ...` with the AGREED question; emergency, stop_request and wrong_person share one request per caller line (as `notices(outbound=True)` does); outcome is a two-line exchange. Question wording is unchanged. Labels: "agreement positive" = label yes; no and unclear both count as not-yes.

Baselines. "today" is what the server does now: the `does that work` gate (`outbound.py` line 601) passes whenever the question was asked, so it predicts yes for every item; there is no server-side detector for emergency, stop or wrong person (`safety_line_spoken` only reads the agent's lines), so "today" never fires. "keyword" is a naive regex I wrote to give a fairer rule baseline (ASSUMED, not in the repo): `gas|smoke|fire|spark|carbon monoxide|chest pain`, `stop calling|take me off|do not call|don't call|remove me|unsubscribe|never call`, and a wrong-number pattern.

## Accuracy, precision, recall at the default thresholds

Thresholds are decide.py's: YES=0.7 for agreement, NOTICE=0.8 for the other three. Baselines use "fired".

| Question | System | Accuracy | Precision | Recall | F1 |
|---|---|---|---|---|---|
| agreement (40) | today regex | 0.575 | 0.575 | 1.00 | 0.730 |
| | OpenAI | 0.925 | 0.955 | 0.913 | 0.933 |
| | Laya | 0.725 | 0.875 | 0.609 | 0.718 |
| emergency (30) | today | 0.667 | 0 | 0 | 0 |
| | keyword (ASSUMED) | 0.633 | 0.462 | 0.60 | 0.522 |
| | OpenAI | 1.000 | 1.00 | 1.00 | 1.000 |
| | Laya | 0.700 | 0.571 | 0.40 | 0.471 |
| stop_request (30) | today | 0.667 | 0 | 0 | 0 |
| | keyword (ASSUMED) | 0.867 | 0.80 | 0.80 | 0.800 |
| | OpenAI | 1.000 | 1.00 | 1.00 | 1.000 |
| | Laya | 0.800 | 0.70 | 0.70 | 0.700 |
| wrong_person (20) | today | 0.600 | 0 | 0 | 0 |
| | keyword (ASSUMED) | 0.850 | 1.00 | 0.625 | 0.769 |
| | OpenAI | 1.000 | 1.00 | 1.00 | 1.000 |
| | Laya | 0.700 | 0.667 | 0.50 | 0.571 |

Outcome (5-way, 20 items): OpenAI 20/20 (1.00), Laya 16/20 (0.80), keyword rule (ASSUMED) 19/20 (0.95).

Reading. The "does that work" gate is right only when the caller said yes: it waves through all 17 non-yes replies (the 57.5% is just the share of yes items). OpenAI lets one through (a reply of "Sure, Monday morning? Yeah?" to the callback question, which I labelled unclear and the model scored 1.0) and holds back 2 real yeses. For the safety questions OpenAI separated the classes completely on this set. Laya misses are mostly plain positives: "I smell gas near the furnace" scored 0.41, "carbon monoxide alarm is going off" 0.77, "take me off your list" 0.66; and it fires on "stop, wait" (0.81) and "my neighbor said his gas company charged him a fee" (0.81). The keyword rule fires on "gas bill" and "gas furnace works" (7 false alarms on emergency) and on "don't call after 9".

Three example misses per question (default thresholds):

- agreement, OpenAI: (1) "Oh. Ok, what now? Sure, Monday morning? Yeah?" labelled unclear, p=1.0 (arguably the label is the debatable one). (2) "Monday at 8am sounds okay. So, you got my name and address right?" labelled yes, p=0.0. (3) "Yeah, that's right. Jess's number is +1 555 0100. So, the tech will come Saturday at 10am?" labelled yes, p=0.63. Pattern: a yes followed by a question back is treated as a question.
- agreement, Laya: "Yep." p=0.34, "yeah" p=0.50, "Yes. That's right." p=0.69; all short yeses it under-scores.
- emergency, Laya: the rotten-egg line 0.55, "I smell gas near the furnace" 0.41, sparks 0.63. OpenAI: none.
- stop_request, Laya: "put me on the do not call list" 0.73, "take me off your list" 0.66; false fires on "stop, hold on a second" 0.90 and "don't call after 9 at night" 0.83. OpenAI: none.
- wrong_person, Laya: "he doesn't live here anymore" 0.34, "I'm just the roommate" 0.32; false fires on "who's calling?" 0.88 and "my wife is here too but I'm the one who got the estimate" 0.91. OpenAI: none.
- outcome, Laya: "Another company already installed it last week" read as not_doing_work; "Hello, this is Carol, Brian's wife" read as still_deciding. OpenAI: none.

## Threshold that maximizes F1, and calibration

Best-F1 threshold is the highest value on a 0.05 grid that reaches the maximum F1. ECE uses 10 equal-width bins on the predicted probability of the positive class.

| Question | Provider | Default thr F1 | Best thr | F1 at best | Precision / recall at best | ECE |
|---|---|---|---|---|---|---|
| agreement | OpenAI | 0.933 | 0.60 | 0.957 | 0.957 / 0.957 | 0.059 |
| agreement | Laya | 0.718 | 0.45 | 0.857 | 0.808 / 0.913 | 0.150 |
| emergency | OpenAI | 1.000 | 0.95 (anything 0.05 to 0.95) | 1.000 | 1.00 / 1.00 | 0.000 |
| emergency | Laya | 0.471 | 0.50 | 0.783 | 0.692 / 0.90 | 0.274 |
| stop_request | OpenAI | 1.000 | 0.95 (anything 0.05 to 0.95) | 1.000 | 1.00 / 1.00 | 0.000 |
| stop_request | Laya | 0.700 | 0.85 | 0.778 | 0.875 / 0.70 | 0.246 |
| wrong_person | OpenAI | 1.000 | 0.85 (anything 0.05 to 0.85) | 1.000 | 1.00 / 1.00 | 0.010 |
| wrong_person | Laya | 0.571 | 0.60 | 0.667 | 0.60 / 0.75 | 0.246 |
| outcome (5-way) | OpenAI | n/a | n/a | n/a | n/a | 0.030 |
| outcome (5-way) | Laya | n/a | n/a | n/a | n/a | 0.242 |

Reading. OpenAI's answers are close to 0 or 1 on this set, so "0.9 means 90%" cannot really be tested: its ECE is low because it is confident and right, not because the middle probabilities were checked (almost none landed in the middle). On agreement the whole error is in the 0.6 to 0.7 band, so lowering YES from 0.7 to 0.6 turns one miss into a hit (a difference of one item; within noise). Laya's probabilities are poorly calibrated (ECE 0.15 to 0.27) and positives and negatives overlap: for emergency, positives start at 0.41 and the highest negative is 0.81, so no threshold separates them. Its best F1 thresholds (0.45 to 0.60) would be bought with false alarms.

Outcome idea C check: with OpenAI, whenever it said still_deciding or price_objection (8 items, at 0.6, 0.7 and 0.8 alike) it was right 8 of 8, and it found all 8 true cases. Laya fired on 6, 4 and 2 items at 0.6, 0.7 and 0.8, all right, but it finds only a fraction.

## Latency (per request, this laptop, sequential, warm; SOURCE)

| Provider | Requests | p50 | p95 | Max |
|---|---|---|---|---|
| OpenAI Decisions (`gpt-6-luna`) | 139 | 161 ms | 358 ms | 1023 ms |
| Laya, MPS, in-process | 139 | 82 ms | 97 ms | 151 ms |

OpenAI includes network. Its p95 (358 ms) is below the 600 ms default timeout in decide.py (the plan proposed 400 ms); 3 of 139 requests took over 600 ms (696, 906, 1023 ms) and would have timed out and failed open at the 600 ms default. No request failed. Most requests carried 3 questions (emergency set) or 1. Laya excludes the 8 to 37 s model load (a 37 s cold load was measured on first run in this session, 8 s on a later warm cache) and two warm-up calls.

## Cost (SOURCE)

OpenAI used 60,797 input tokens across 139 requests (plus one warm-up and two earlier curl probes, about 450 tokens uncounted), which is $0.0061 at $0.10 per 1M. Output is free. Each request carries the full instruction text, so the three-question requests cost about 350 tokens and one-question requests about 160 to 200. Laya cost nothing.

## What to ship

- A (caller-answer check): OpenAI. Use YES=0.7 as it is; 0.6 is better by one item. A false yes (letting a hedged or corrected reply through) is the costly error, so do not lower it based on this sample. Treat "ok but a question back" replies as unsure and re-ask.
- B (reflex notices): OpenAI at NOTICE=0.8 for all three predicates. Laya is not shippable for B on this evidence.
- C (outcome second opinion): OpenAI. Override `lost` only when the model is at least 0.7 confident in still_deciding or price_objection.

## UNVERIFIED

- Everything is on 140 items, mostly written by me and labelled by me; small sets and single labeller. A 100% on 30 items is a lower bound of about 0.9 at 95% confidence (ASSUMED rule-of-three), not proof.
- No human caller audio or STT output was tested; real transcripts will have dropped words and "three oh three oh nine" style numbers. Only sim text and db lines (which are sim or audiotest text).
- The stress call is not in the db, so "too expensive recorded as lost" was written as variants, not replayed.
- Calibration at 0.5 to 0.7 for OpenAI is untested because it hardly produced such values here.
- Laya is the stock checkpoint, not the vendor's typed-decisions fine-tune, and not fine-tuned on Summit transcripts; a fine-tune could change this verdict.
- Laya was run through a copy of `_laya`'s translation code in the Laya venv (the venv has no httpx), not by importing `app/decide.py`. The OpenAI side did go through `decide.ask`.
- The keyword baselines are my guesses at a reasonable rule, not repo code.
- wrong_person lines were scored without the agent's question ("Is this Brian?"), as `notices()` does today; lines like "yes this is Marcus" depend on that missing context.

## Note on decide.py

`_laya` parses the response shape correctly: `{"answers": {name: {"noul": p}}}` and choice answers with `choice` and `probabilities` (verified on a live call). One thing to fix if choice confidence is ever used: Laya's `confidence` for a choice is not the probability of the chosen option (a call with probabilities 0.80 / 0.20 returned `confidence` 0.27 and `answer_confidence` 0.80). Read `answer_confidence`, or `probabilities[choice]`. OpenAI's parse is correct. Nothing in the repo was changed.
