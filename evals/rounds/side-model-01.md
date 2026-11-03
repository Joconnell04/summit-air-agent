# Side model 1: the vulnerable-person read

Bake-off 3 in `docs/bakeoffs.md`, the quick run from `evals/decision-models/PLAN.md` on one question.

**Bottom line.** The phrase rules that raise priority for someone at risk at home miss about a third of the ways callers say it. A side model asked "is someone at the home older, a young child, pregnant, or ill in a way that makes heat or cold dangerous?" closes that gap on the test lines with almost no new false alarms. OpenAI Decisions is the one built in as the default to switch on: it met every accuracy bar, supports zero data retention, and its answer was back before the priority gate on every one of 47 gate reads. It is behind `SIDE_MODEL`, off unless set, and a late or failed answer leaves today's rules to decide. Every number is MEASURED by the runs named here unless marked.

## Why this judgment

- **The gap is real and large.** On 80 lines written by a subagent that never saw the phrase lists (`evals/decision-models/lines/vulnerable_person.jsonl`), the rules found 24 of 37 people at risk (recall 0.65). They missed "my pop turned ninety last month", "my wife's seven months along", "nana lives with us and she's not well", "she's on dialysis" as speech-to-text spells it, "my son is two", "im 74 and i live alone" (the age pattern needs the apostrophe in "I'm"), and seven more. The plan's bar for this question is recall 0.95.
- **The eval suite hid it.** Every priority scenario passed on both models in the model matrix (`evals/rounds/model-matrix-01.md`), because each uses a cue the lists already contain. A new rigorous scenario, `stroke-in-passing-no-heat` (a mother-in-law who moved in after a stroke, no age given), books at High instead of Urgent on all three runs without a side model (`evals/results/20261103-221658-livekit-fast-custom.json`).
- **It is the assignment's own example** ("no heat in January with an elderly person in the house") and the side model can only raise priority, so the safe direction is built in.
- **Other candidates, and why not first.** The residence check showed one failure mode in the matrix (Sonnet asking about the building and a renter saying "apartment building"), but no Haiku failure in 64 calls; it is the next candidate. The gas check answer is a safety judgment where a side model could only add yeses; fixing an over-firing check would mean letting a model stand the safety lines down, which a model-set flag must never do. The main model does not decide priority at all here; the server's phrase rules do, so this is the server's judgment being improved.

## Method

- **Lines.** 80 caller-side snippets (1 to 3 lines each), 40 at risk (15 explicit, 25 soft) and 40 not (25 hard negatives such as "my mom is 80 but she lives in florida", 15 ordinary), 15 with speech-to-text noise. Five are tagged borderline and left out of the scores, so 75 are scored. One writer, no second labeller.
- **Systems.** `rules`: the tenant's eleven priority rules about people (`vulnerable_person`, `medical_equipment`, `young_child`, `pregnancy`, `recovering` and the rest), read offline. `haiku`, `openai-decisions`, `jev`: profiles under `side` in `config/models.yaml`, called through `app/side_model.py`, the code the tool server uses, with the same question wording (`prompts/side/questions.yaml`). One request at a time, from a laptop.
- **Run.** `make eval-decisions`, results in `evals/decision-models/results/20261103-220655-vulnerable_person/` (`summary.md` lists every miss). Threshold 0.5, not tuned (no dev and test split in a quick run). Cost $0.0075. A first attempt failed every Haiku call (`temperature` is not accepted by Haiku 5.5); it was fixed and the whole run repeated.
- **Paid check in the full harness.** Haiku as the live brain, `SIDE_MODEL=openai-decisions`, every priority scenario once plus the new scenario three more times: `evals/results/20261103-221756-livekit-fast-custom.json` and `evals/results/20261103-221922-livekit-fast-custom.json`, $0.34. Side-model timings from the server's `summit.side_model` log lines.

## Results

| System | Recall alone [95%] | Precision alone | Recall with rules | Precision with rules | Fixes | New alarms | p50 / p95 / max |
|---|---|---|---|---|---|---|---|
| rules (today) | 0.65 [0.49, 0.78] | 0.80 | 0.65 | 0.80 | | | 0 ms |
| haiku | 0.97 [0.86, 1.00] | 1.00 | 1.00 | 0.86 | 13 | 0 | 1018 / 1395 / 1726 ms |
| openai-decisions | 1.00 [0.91, 1.00] | 0.97 | 1.00 | 0.84 | 13 | 1 | 223 / 511 / 777 ms |
| jev | 1.00 [0.91, 1.00] | 0.97 | 1.00 | 0.84 | 13 | 1 | 68 / 120 / 703 ms |

"With rules" is how the server uses it: a rule's raise stands and the side model can only add one. Fixes are people at risk the rules missed and the side model found; the one new alarm for both dedicated models is "my neighbor is 90 and she told me to call you". Exact McNemar p against the rules is 0.0002 for Haiku and 0.0018 for the other two.

**In the harness.** 15 calls asked the side model 195 times (once per caller line) and the priority gate read an answer 47 times. None was late and none failed; the gate waited 0 ms every time, because the answer had come back while the agent was still talking. OpenAI Decisions answered in 219 to 281 ms at p50, 489 to 728 ms at p95. Every judged priority scenario passed (10 of 11, the miss a grader note on medical advice in `asks-agent-to-call-911`), and `stroke-in-passing-no-heat` booked Urgent on all three judged runs (a fourth ran out of caller turns after booking). Asking on every caller line costs about $0.0004 a call at the measured token use.

## Against the plan's pass rules (PLAN.md section 5)

| Rule | haiku | openai-decisions | jev |
|---|---|---|---|
| 1. Recall 0.95+, soft 0.90+, no explicit miss, precision 0.50+ | yes (one soft miss, "my dad uses a wheelchair", 0.35) | yes | yes |
| 2. 5+ net fixes, McNemar p 0.10 or less, alarms up 5 points or less | yes, 13, alarms flat | yes, 13, alarms up 2.6 points | yes, 13, alarms up 2.6 points |
| 3. p95 400 ms or less (ASSUMED bar) | no, 1395 ms | no on the bar, 511 ms; but 47 of 47 back before the gate | yes, 120 ms |
| 4. A dedicated model must beat Haiku | | not on accuracy, on speed | not on accuracy, on speed |
| 5. Zero retention or a written answer | yes (Anthropic) | yes, supported (SOURCE: docs/decisions/05-pipeline-and-models.md) | no answer yet, so it fails until one comes |

Rule 3's 400 ms bar was set for an answer requested at the gate. Built as asked on each caller line, what matters is whether the answer is back when the gate reads it, which was 100% for OpenAI Decisions. Haiku at a 1.0 s median would often still be on its way when the agent calls `find_slots` after a short reply. Jev is the fastest and equally accurate on this set; it becomes the better choice if TypeSafe gives a written zero-retention answer.

## What was built

- `app/side_model.py`: the three backends, and a `SideModel` that asks on each caller line (`prefetch`) and gives the priority gate whatever came back within 0.4 s (`WAIT_S`, ASSUMED). A late, failed or missing answer is `None`, and the rules decide alone.
- `app/main.py`: the caller-turn endpoint asks the side model when the tenant's slot is set and `SIDE_MODEL` names a profile. `app/priority.py`: the slot names its question and job types, and raises like the `vulnerable_person` rule (Urgent, High on a mild day). `tenants/summit-air/agent.yaml`: the slot, for no heat and no cooling only.
- `SIDE_MODEL` is empty by default, so nothing changes until the server is started with `SIDE_MODEL=openai-decisions`. Not deployed.
- Tests: `tests/tool_server/test_ts_side_model.py` (fake model and fake HTTP; no network) and `tests/evals/test_decision_models.py`.

## Open

- The side model raises soft cues to Urgent where the rules give some of them High (a young child, pregnancy, recovering from surgery); it answers one question and can't tell the tiers apart. A proposed decision in the PR.
- Not run yet: the full plan (480 lines, a second labeller, the main-model arm, the Spanish slice), a threshold chosen on dev lines, and latency from the Vultr host.
- The age pattern in `app/priority.py` misses "im 74" with the apostrophe dropped, as speech-to-text often writes it. A one-line fix, separate from this.
