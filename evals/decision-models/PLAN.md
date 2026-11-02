# Decision model test suite: plan

Status: design only. Nothing built or run; no paid calls. Feeds C-35 and part 3 of `docs/decisions/05-pipeline-and-models.md`.

**Bottom line.** One labelled set (480 lines, four questions), five systems behind one function, scored side by side. A side model is adopted for a question only if it clears that question's bar and beats the launch setup (fixed phrase list plus the main model) on the costlier error, with no delay a caller would notice. Plain Haiku wins ties. "Nothing beats launch" is a valid result.

## 1. Questions and what a wrong answer costs

Each question reads a two-turn window (the agent's last turn and the caller's reply). The model states a fact; the server decides what to do. Bars are ASSUMED, set from the costs, and confirmed before the first full run.

| Question | Costly error | Bar (test split) | On timeout |
|---|---|---|---|
| `agreed_readback`: did the caller clearly agree to the read-back? | False yes: booking on a wrong address, phone or time. A false no costs one re-ask. | Precision 0.97+, recall 0.85+ | Not agreed; re-ask |
| `vulnerable_person`: did the caller say someone at the home is older, a baby or young child, or has a health condition? (Fact only; the server joins it to no heat or no cooling.) | A miss. A false alarm costs a flag and a slot. | Recall 0.95+, soft cues 0.90+, no explicit miss; precision 0.50+ | Yes; raise priority |
| `commercial` (optional): is the call for a business or a building they manage? | Both: declining a homeowner, or booking a job Summit Air does not serve. | Precision 0.95+, recall 0.85+ | Abstain; ask one question |
| `asked_for_person` (optional): did the caller ask for a person? | A miss: a trapped caller. | Recall 0.95+, precision 0.80+ | Yes; offer a person |

## 2. The labelled set

| Question | Lines | Positives | Hard negatives | Other negatives | Spanish |
|---|---|---|---|---|---|
| agreed_readback | 120 | 40 | 40 | 40 | 24 |
| vulnerable_person | 200 | 80 (40 explicit, 40 soft) | 60 | 60 | 40 |
| commercial | 80 | 30 | 20 | 30 | 16 |
| asked_for_person | 80 | 30 | 20 | 30 | 16 |
| Total | 480 | 180 | 140 | 160 | 96 |

- **Hard negatives.** "yes, but it's 4B", "yeah? and the tech comes Saturday?", "my mother used to live there", "the baby's room is fine, it's the boiler", "I own a two-family and live in it", "are you a real person?". Lines the call journeys leave open (a small landlord) are tagged `borderline`, reported, never scored.
- **Spanish and noise.** Spanish is 20% (about one in four Queens and Brooklyn residents speak English less than very well; SOURCE: `docs/users.md`). 20% of lines are speech-to-text style (no punctuation, dropped words), made by a seeded script. 20% are deliberately middle-ground, because Bobert's OpenAI scores sat near 0 or 1 and calibration went untested.
- **Size and split.** Zero misses in 60 test positives bounds the miss rate under 5% at 95% (ASSUMED rule of three; Bobert's 100% on 30 lines was only a floor near 0.9), hence 80 vulnerable positives. 25% of lines are dev (wording, thresholds); 75% are test, frozen by hash. Each question's wording states the answer when the situation never came up and names the exceptions (Bobert's grader lesson).
- **Sources.** Written from `docs/users.md` and the call journeys, hard cases drafted by the `red-team-caller` agent: about 330. The earlier agent's 40 agreement items, relabelled: about 50. Eval simulator caller lines once the harness exists (decision 002): about 80; written lines stand in until then, tagged and scored separately because written text is tidier. Transcripts of the roughly 10 planned audio calls: about 20, for real speech-to-text noise.
- **Labelling.** Written lines carry an intended label. The grader profile labels every line blind to it (about $1.50 once, ASSUMED) and a person settles each disagreement. A person also labels a random 15% blind; Cohen's kappa under 0.8 means `LABELLING.md` is tightened. Spanish needs a Spanish-speaking reviewer; without one, Spanish results are marked unverified and cannot carry a promotion.

## 3. Systems and the one interface

| System | Call |
|---|---|
| Launch baseline | Phrase list plus the real agent prompt and tool (`fast` profile), reading the flag it sets. Agreement: list AND flag. Others: list OR flag. List alone also scored. |
| `haiku_side` | Separate narrow call, JSON `{answer, confidence 0 to 100}`, temperature 0, thinking off, new `side` profile |
| `openai_decisions` | `POST /v1/decisions`, `gpt-6-luna`, predicate returns a probability |
| `jev` | `POST /v1/systemone`, `jev-1.14.0`; needs an account and key |
| `laya` | Self-hosted typed-decisions checkpoint. Stock Laya is out (recall 0.40 on emergencies, MEASURED by Bobert). Drop if it misses over 1 in 5 explicit vulnerable lines in the quick run. |

```
decide(question_id, window, timeout_ms) -> {answer: bool, confidence: float, latency_ms: int,
                                            status: ok|timeout|error, fallback: bool}
```

`confidence` is the probability of yes; yes/no-only systems return 1.0 or 0.0 and get no calibration score. A wrapper in server code, not each adapter, enforces the timeout and substitutes the safe answer from section 1. Wording, windows, timeouts and safe answers live in `questions.yaml`; model ids in `config/models.yaml` (proposed, not applied).

## 4. Metrics

Per question, system and slice (English, Spanish, hard, noisy, simulator versus written): precision, recall, F1 with 95% Wilson intervals; Brier score and ECE (10 bins) where a probability exists. Thresholds are picked on dev, applied to test. Paired comparison on identical lines against the baseline and against Haiku: fixes versus breaks, exact McNemar test. Every miss is printed with its line.

**Latency, off the reply path.** The decision call fires at the same instant as the main model call, as it would live. Report p50, p95, max, timeouts at 600 ms (ASSUMED; replaced by the budget from #7), cold start (first call after 60 s idle, 10 samples), and the before-gate rate: the share of lines where the answer is back before the main model's next tool call or reply. 300 timed requests per system from the Vultr host, plus once from a laptop. For scale, OpenAI measured p50 161 ms, p95 358 ms, 3 of 139 over 600 ms (MEASURED by Bobert, laptop, sequential).

## 5. Pass rules (all must hold, per question)

1. Bar met in English. Spanish recall within 10 points of English (ASSUMED), or the system runs for English only; the server knows the language.
2. Beats the launch setup: at least 5 net fixes on the costlier error, McNemar p of 0.10 or less (ASSUMED), alarms up no more than 5 points.
3. p95 400 ms or less, timeouts 1% or less, before-gate rate 99% or more (ASSUMED).
4. A dedicated model must also beat `haiku_side` by rule 2; otherwise Haiku wins (same vendor and key, no new processor).
5. Zero retention or a written retention answer, since lines carry names and addresses (C-30). Jev fails until answered.
6. Haiku run three times agrees with itself on 97% of lines or more; otherwise use a three-vote majority.

**Replaces.** `agreed_readback`: the main model's `confirmed` flag (list AND winner). `vulnerable_person`: the main model's judgment of soft cues (list OR winner). `commercial`, `asked_for_person`: the main model's judgment beside the signal list. The fixed list is never removed.

## 6. Cost and size of one run

| Run | Size | Cost |
|---|---|---|
| Quick (default) | 120 test lines, 30 per question, 1 sample. A wiring and sanity check, not evidence. | about $0.08 |
| Full | 480 lines, 3 samples on the two Claude arms, plus 300-request latency runs | about $1.10 |
| Label cross-check, once | 480 lines on the grader profile | about $1.50 |

Prices are SOURCE (`docs/decisions/05`): Haiku 5.5 $0.10 in and $0.50 out per million tokens, OpenAI $0.10 in, Jev $0.042 in. Tokens are ASSUMED: about 500 in and 15 out per side call, about 5,000 in and 100 out for the main-model arm (`06-eval-design.md`). Per 1,000 live calls: Haiku side about $0.06, OpenAI about $0.04 (MEASURED by Bobert), Jev about $0.01, main-model arm about $0.55. The runner prints its estimate first and stops at `--max-usd` (default 2.00). Labelling time is the real cost.

## 7. Files, Makefile and results

```
evals/decision-models/  PLAN.md  LABELLING.md  questions.yaml  run.py  score.py
  lines/<question>.jsonl   id, window, label, tags, source, split, second_label
  systems/                 baseline, main_model, haiku, openai_decisions, jev, laya
  results/<run-id>/        results.json, summary.md
tests/test_decision_models.py   offline: line files valid, wrapper timeout gives the safe
                                answer, scoring on a fake system, no key means "not run"
```

`make eval-decisions` runs the quick set and prints cost first; `FULL=1` runs everything; `make eval-decisions-rescore RUN=<id>` rescores cached answers free. `make check` stays offline.

**Into DECISIONS.md.** A full run commits `results.json` (with a hash of lines, questions and system versions) and `summary.md`. Entry 003 cites them and says per question either "adopt X, replacing Y" or "stay on launch". Then C-35 is marked decided, decision 05 part 3 is updated, and a winner gets its `models.yaml` profile, floors in `evals/baseline.json`, and a build brief.

**Order, by dependency.** Lines and `LABELLING.md` first; they need nothing else. The baseline arm needs the real agent prompt and tool, the latency run needs the Vultr host, and Jev needs an account and key.
