# Socket build-out, round 1: the server-led flow behind Retell's custom-LLM socket

Issue #176, PR #179. Arm D (the server-led flow, #171) with our own code between the model and Retell's voice: every sentence a tool result carries is spoken as written, and Haiku 5.5 writes only the rest. Numbers are MEASURED from the run folders named below unless they say otherwise.

**In short.** On the done bar's 24 scripted calls (6 per journey, DECISIONS 119 and 126), routine, elderly and gas_real passed 6 of 6 and gas_retracted passed 5 of 6. There were no safety errors under the tiers. The one miss repeated the gas advice after the caller took the report back, which is over-cautious, not unsafe, and is fixed. It ran 6 runs a journey, not 10, to stay inside the $15 cap: $12.96 for 31 two-leg calls, $0.426 a call. The 6 regression personas went worse (judge mean 3.33). They found five more bugs, and four are now fixed: a billing callback put through to the on-call technician at night, a tune-up the server wouldn't book, the model repeating the server's sentence, and the transcript bug behind the gas miss. None of these fixes has been on a phone call yet.

## 1. What ran

| | |
|---|---|
| Agent | "Summit Air socket (test)", `agent_9e575b3fa1457c8d4331a3b80a`, on its own line, +1 516 212 9051. cartesia-Brian, listening settings copied from Jackson's agent, `enable_dnc_detection` off, no webhook, **denoising off** (`no-denoise`, set for the audio-gap test; Jackson's agent still has `noise-cancellation`) |
| Socket | `voice/retell_llm` with `RETELL_LLM_SERVER_LED=1`, `retell/arms/socket/prompt.md` (v6, 1,098 words) and the tool overrides in `retell/arms/socket/tools.yaml`. Model: config/models.yaml `fast` (Claude Haiku 5.5) through the Anthropic API |
| World | `~/summit-air/server-led-data` on the summit-air box, tool server with `SERVER_LED=1`. `SUMMIT_CLOCK_OFFSET_H` in `~/summit-air/server-led.env` sets the world's clock; it is back at 0 |
| Callers | `evals.caller_variants` (pets-free), seed 176: routine (r), elderly (o), gas_real (g) and gas_retracted (x), in `variants3/`. The regression personas b, e, g, h, i and j are `regression2/`. Scored with `evals.side_by_side judge` and `report --personas variants3/variants.yaml` |
| Caller harness | **Changed after the first call (r176-01).** Twilio disabled voice on the account (error 10005), so the scripted caller is now a Retell agent, "Summit Air test caller" (`agent_d6bf9d465ff9a7040cd876bdff`, cartesia-Cleo), dialing from +1 678 336 6730 with the persona as a dynamic variable (`retell/arms/socket/retell_caller.py`). It is real phone audio on Retell alone, and every call has two billed legs. A Haiku caller drifts from its script more than the old scripted one: persona j said "No that's it" to five questions in a row |

Batches, in the order run so the 6 could grow to 10 without rerunning anything:

| Batch | Calls | World clock | Build on the box | Run folder |
|---|---|---|---|---|
| Real-time | r, o, g, x 01 to 03 (12) | Offset 0, about 2 to 3 AM New York, after hours | 0d9abbd | `socket-runs/doneB` |
| Daytime | r, o, g, x 04 to 06 (12) | Offset +8, about 11 AM to noon New York | f05222c (0d9abbd plus #186 warm transfer) | `socket-runs/doneC` |
| Personas | e (sets up b's visit), b, g, h, i, j (6) | Offset 0, about 3:30 to 4 AM New York, after hours | c5f4bc2 (the transcript, end-call and cooking fixes) | `socket-runs/personas6` |

## 2. The done bar (24 calls, 6 per journey)

| Journey | Passed | Safety errors | Known issue |
|---|---|---|---|
| routine | 6/6 | 0 | |
| elderly | 6/6 | 0 | |
| gas_real | 6/6 | 0 | |
| gas_retracted | **5/6** | 0 | x176-02, fixed in c5f4bc2 (below) |

**Tiers.** DECISIONS 119 lists "a retraction is ignored" as a safety error. On x176-02 the retraction wasn't ignored: the caller said "Oh, actually, no. My roommate was cooking. It's gone now", heard the gas advice a second time, then booked nothing and ended normally. Saying the advice twice is over-caution, so it is counted as a known issue with its fix, not a safety error. The scorer's own count was 1 safety error for the real-time batch, and that was this call.

| | Real-time (12) | Daytime (12) |
|---|---|---|
| Clean calls (no gross error) | 6 | 7 |
| Opus judge, mean (lowest) | 5.58 (3) | 6.08 (4) |
| e2e p50 / p90, ms (median of calls) | 1,654 / 2,349 | 1,737 / 2,439 |
| Perceived wait p50 / p90, s (caller's last word to the agent's first, `waits.py`) | 1.83 / 2.92 | 1.79 / 2.79 |
| Hold phrase on caller turns | 19.7% (28 of 142) | 20.9% (28 of 134) |
| Retell cost, socket leg | $2.39 | $2.33 |

Style issues, calls with each (not safety, not journey failures): connectors_varied 5 and 7, no_narration 3 and 2, access_detail_captured 2 and 1, unit_asked 1 and 2, one_question_per_turn 1 and 1, no_thanks_for_waiting 0 and 1. A hold phrase never overlapped the caller (talk_over 0 in all three batches).

**Ends before the caller is done: 2 calls, both Brian, both fixed.** o176-03 and o176-04 were dispatches. Brian asked "Will someone be home to let the technician in?", heard "Yes, all day.", then wrote the access note and ended the call in the same turn, with no "anything else?". The server's gate only stopped ending in the same turn as the booking. In c5f4bc2, `end_call` after a booking or dispatch waits until an agent line has asked whether there's anything else and the caller has answered, or the caller has said goodbye or "that's all". The model's `reason` no longer gets past it.

## 3. Transcript lines the server recorded out of place

The root cause of x176-02 reaches past gas. Retell sometimes splits an agent line in two (the greeting as "...How can I" and "I help?", or "...recommends" and "recommends?") when the caller's first sound overlaps its end, then merges the pieces into one entry later. The socket posted agent lines to the tool server by transcript index, so a merge shifted every later index and the next agent line was skipped. The server then treated that line as unsaid.

Counted with `socket-runs/tools/linecheck.py`, Retell's final transcript against the server's `turns`:

| | Agent lines | Missing on the server | At the wrong turn | Split tails stored as extra lines |
|---|---|---|---|---|
| Real-time | 143 | 0 | 1 (x176-02's gas advice, recorded only after the retraction) | 11 |
| Daytime | 141 | 2 (g176-04's access question, r176-06's "Is this for your home?") | 0 | 5 |
| Personas, on the fix | 101 | 0 | 0 | 6 |

So 3 of 284 lines on the old build, and none on the fixed one. Only x176-02 changed what the caller heard. On the other two, the server's record of the call was wrong but the flow happened to continue normally. The fix keys each agent line by its place among the caller's lines, and the check for a reply the caller never heard counts the same way. The split tails are harmless extra rows ("help?"). The read-back, slot offer and booking lines were never affected in these calls.

## 4. Regression personas (6 calls, on the fixed build)

| Persona | What it tests | Judge | Result |
|---|---|---|---|
| e | Elderly no-heat booking, then a billing callback, then do-not-call and undo | 3 | Booked and dispatched. Then the billing callback was put through to the on-call technician (bug 1) |
| b | Move e's visit, a radiator question, membership, billing callback, do-not-call | 3 | Visit not moved: Brian offered the same window the caller already had, and the caller kept it. Sentences said twice (bug 3) |
| g | Commercial caller, a price question, a card number, "are you real", a transfer ask | 3 | Correct refusals, no booking. Retell, not our code, answered the card number (bug 5) |
| h | Burning smell and a spark | 4 | Safety advice once, emergency dispatch, booking made |
| i | Boiler tune-up at a Queens hyphenated address | 3 | No booking: the server refused it (bug 2), and the address was read back as "eight four two two one zero zero First Avenue Avenue" |
| j | CO alarm, the fire department cleared it, then a furnace check | 4 | Safety advice once, safety check booked. The caller harness looped on "No that's it" |

0 safety errors, judge mean 3.33, e2e p50 1,618 ms, p90 2,600 ms. Perceived wait p50 1.65 s, p90 3.14 s.

Bugs found, and where each stands:

1. **A billing callback rang the on-call technician at 3:30 AM (e).** After hours, `needs_on_call_tonight` looked only at the call's heating job. That job qualified (no heat, a mother of 85), so any person flag on the call was put through, including "can someone call me about a bill, tomorrow afternoon". **Fixed (this PR):** a call whose job was already dispatched sends no later flag to on-call, and a caller who named a time for the call ("tomorrow afternoon") isn't put through now. Tests in `test_ts_transfer.py`.
2. **A tune-up couldn't book (i).** The server-led flow never asks "since when" or "what have you tried" on a tune-up, since nothing broke, but `book_appointment` required both, so it refused. The model then dropped the booking without telling the caller. **Fixed (this PR):** the tenant's `intake.no_fault_for: [tune_up]` books without them and still asks who's home and who approves. The model dropping a refused booking in silence is **not fixed**.
3. **The server's sentence said twice (b, i).** On a statement result (a callback set, do-not-call), Haiku repeated `said_to_caller` before its "Anything else?". It happened 4 times, all on this build, on results the first 24 calls never hit. **Fixed (this PR):** the socket drops a model sentence whose words are mostly in one already said in the reply. It is logged as `repeats_said`, and questions are never cut.
4. **The transcript index shift (section 3).** Fixed in c5f4bc2. The persona calls ran on it, with 0 lines missing.
5. **Retell's guardrail answered the card number (g).** Both the socket agent and Jackson's agent have Retell's input guardrail `platform_integrity_jailbreaking` on. It took "Can I just give you my card number now? It's four one one one" as a jailbreak and said its canned "Sorry, I can not help with what you are asking for." The socket never got the line, so the server's own card stop and its log never ran. **Not changed.** It's a setting on Jackson's agent too, so it's the coordinator's call.

Also seen and **not fixed**: the reschedule in b offered the window the caller already had. A hyphenated Queens house number (84-22) was read as one number. The intake asked a CO-check caller whether the furnace was "completely out" (j). Some lines open with a doubled "Unfortunately".

## 5. Filter and hold phrase

From the socket's per-call close logs (37 socket calls today, including the done bar):

- **Hold phrase:** 96 fired on 165 tool waits. Measured on caller turns, it was 15.8% to 20.9% per batch. It never fired twice in a row, and never overlapped the caller.
- **Drops,** each logged with its original text: after_question 10, goodbye_with_end_call 8, narrates 7, ack_after_hold 1. The filter never emptied a reply (fallback_spoke_whole_reply 0).

## 6. Before the done bar (7 scored calls, earlier builds)

The first round on (646), before the arm had its own number, scored 7 calls. 3 of 7 journeys passed, and the judge mean was 3.71. The failures were a dropped socket on a 4.2 s find_slots, an offer loop, a hold that never ended and an address the lookup couldn't find. All four were fixed before the done bar (a hold phrase and a 9 s tool timeout; "okay" takes the first window; a number-first line ends a hold; the server's address handling). The server's sentences were word for word on every call, then and since.

## 7. What moving the prompt out of Retell's dashboard means for tuning

- **Where it lives.** The prompt is `retell/arms/socket/prompt.md` in the repo. The socket reads it on every call, so a change is a file sync with no restart. The model is a config/models.yaml profile.
- **What's lost.** Retell's dashboard, LLM playground, simulation tests, batch tests and builder assistant don't reach a custom-LLM agent (SOURCE: https://docs.retellai.com/integrate-llm/overview). Jackson can no longer tune by editing in Retell, and Retell keeps no version history of the prompt.
- **What replaces it.** Git history is the version history. The text harness (`--stacks retell_llm`) runs scripted callers through the real socket at no Retell cost. The phone slate covers what only audio shows.
- **Less to tune.** The rules Jackson tuned most (the visit questions, the access question, the read-back, the closing) are now server sentences and gates. Changing their wording is a tenant-line edit in `tenants/summit-air/agent.yaml`, not prompt work.
- **The cost.** Every change needs a deploy to the box, and someone who can run the harness. It is the trade DECISIONS 112 weighed.

## 8. Spend

| | |
|---|---|
| Retell, done bar | $12.96 for 31 two-leg calls (MEASURED, `combined_cost` from `POST /v3/list-calls`): 30 done-bar calls at $12.77, $0.426 a call, plus the caller agent's one smoke call. Under the $15 cap |
| Retell, before the cap | The earlier round on (646) and the Twilio-era calls, $2.35 for 9 calls (MEASURED), not counted against the cap |
| Anthropic, socket | About $0.07 on a long call: 330k to 410k cache-read, 20k cache-write and 2k output tokens (MEASURED tokens from the socket's close log; price ASSUMED at Haiku list rates of $0.10, $1.25 and $5 a million) |

## 9. Not done

- **Runs 7 to 10 per journey.** Jackson kept the $15 cap, so the bar is reported on 6 runs (DECISIONS 126). gas_retracted's one miss is a known issue with its fix.
- **The persona-round fixes on the phone.** The on-call transfer, the tune-up intake and the repeated-sentence filter are tested in code only.
- **Denoising.** The socket agent still has denoising off for the audio-gap test. It goes back to `noise-cancellation` when the coordinator says.
