# Side-by-side 1: four agent arms on the real phone line

Issue #169, brief `docs/ai-process/briefs/side-by-side.md`. Scripted calls over the real line to four Retell agents, scored by our own checks, an Opus 5.5 judge and Retell's own latency and cost. Every number here is MEASURED from `side-by-side-01-data/results.json` (how is in Method) unless it says otherwise.

**Recommendation: build on D, the server-led arm, and put it behind the custom-LLM socket so code speaks the server's lines; keep Haiku 5.5.** D isn't the best arm today: B (Sonnet 5.5) is, with 3 clean calls of 8 and the fewest gross errors (8). But every arm's errors that stand between it and the done bar (DECISIONS 119) come from the model writing words the server already owns. D already holds the call order in code. With the socket, the server's sentences, the safety lines and the open door are spoken as written, and names, narration and echoes are filtered before the caller hears them. That removes those error classes by construction, on the cheaper and faster model. Sonnet halves the gross errors but costs 3.5 times as much a minute and adds about 0.4 s to every reply. It still narrates its own tools, and it wins nothing on safety, so the idea of promoting a call to Sonnet when a safety trigger fires is dropped. The behavioral prompt (C) did worst on every measure and is dropped.

No arm made a safety error under the DECISIONS 119 definition: 0 on all four.

**For the demo in front of Paul today, a separate question from the ceiling: B (Sonnet 5.5) on the fixed server, with A as the fallback.** Against the done bar, B came closest. It made no safety errors, had the fewest gross errors (8 against A's 15), and handled the retracted gas call best (judge 8). Its worst moments were reading out tool limits that 28707de removes (the weekday callback, the narrating refusals). That still needs one check call on 28707de, because the placeholder-ZIP line isn't fixed. The extra 0.4 s a reply and the cost don't matter for one session.

## What ran

| Arm | Agent | Model | What differs from A | Line and world |
|---|---|---|---|---|
| A, as-is | agent_a3e38e6aa3d4e75dfac12aafc8 | Claude Haiku 5.5 | Nothing (Jackson's agent) | (646) 624-0418, live |
| B, Sonnet 5.5 | agent_871ef6fafe2636244c4bbaca67 | `claude-5.5-sonnet` | Model only; from 01:45:57 UTC also one prompt line (see B below) | (678) 336-6730, eval |
| C, behavioral Haiku | agent_d72df072302c81f75d1b7fadf4, and agent_eb7032636866b9c563b86bc846 for its c, l and i reruns | Claude Haiku 5.5 | General prompt only: `retell/archive/haiku-behavioral/prompt.md` from PR #172 (1,093 words against 2,254) | 646 live; c, l, i reruns on 678 eval |
| D, server-led | agent_e758054f5009c5106edd882d81 | Claude Haiku 5.5, temperature 0 | Its own tool server with `SERVER_LED=1`, bridge and mock (PR #173), 828-word prompt | 678, server-led |

B and C were made by `voice/retell/side_by_side/make_arm.py`, which copies every setting of A and changes one thing; a diff of each copy against A showed only the intended fields. Two calls at once never shared a server world. The live and eval worlds run the same code (`diff -rq` clean) and were reset the same way, with `scripts.reset_live_world` against each world's databases (both opened the same 84 shifts).

**Personas.** Eight core personas, one call each per arm, in the same order: e (no heat, an 85-year-old mother, a billing callback, do-not-call and its undo), b (move the visit e booked, radiator question, membership "no thanks"), c (gas, then retracted), g (commercial, price, card number, "are you real", transfer), h (sparks, renter, a corrected phone number), i (Queens hyphenated address, Saturday tune-up, a Monday callback), j (CO alarm cleared, then a visit), k (Siobhan Nguyen, no heat with a newborn). Added for the done bar: l, a real gas smell. The persona scripts are in `evals/side-by-side/personas/`, and their expected records in `personas.yaml`.

**Reruns.** e and b were rerun on A, B and C after Retell's do-not-call detection was turned off; do-not-call handling is scored from those only. c was rerun after its caller broke role, and i after its caller read its script aloud. A later valid call replaces the earlier one for that arm and persona. Every change during the run is in `side-by-side-01-data/run-notes.md`.

**After hours.** Every call ran after 8 PM New York time, so every urgent no-heat call took the on-call path. That's the same for every arm. The daytime booking path is covered only by the earlier scripted rounds.

## Results

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

| Check (calls failing it) | A | B | C | D |
|---|---|---|---|---|
| Server's lines not said as written (`server_lines_verbatim`) | 5 | 3 | 8 | 6 |
| Says the caller's name (`no_caller_name`) | 1 | 0 | 7 | 2 |
| Narrates its own steps (`no_narration`) | 0 | 2 | 5 | 1 |
| Two questions in one turn (`one_question_per_turn`) | 3 | 1 | 2 | 1 |
| Ends the call before the caller is done (`no_premature_hangup`) | 0 | 0 | 4 | 1 |
| Expected booking missing (`records_bookings`) | 2 | 2 | 3 | 2 |
| Raise didn't reach the booking (`records_priority_raised`) | 1 | 0 | 2 | 1 |
| No open door with the first offer (`open_door_with_first_offer`) | 1 | 0 | 0 | 2 |
| Open door reworded (`open_door_verbatim`) | 0 | 0 | 0 | 2 |
| Callback on the wrong day (`callback_on_the_day_asked`) | 1 | 0 | 1 | 1 |
| Number left on the do-not-call list (`records_dnc_undone`) | 0 | 0 | 2 | 0 |
| Confirmed more than the persona earns (`no_double_confirm`) | 1 | 0 | 0 | 0 |
| Safety style: own safety question (`no_own_safety_question`) | 0 | 0 | 2 | 1 |
| Safety style: advice repeated (`safety_advice_once`) | 1 | 0 | 1 | 2 |

Every call with its checks and judge verdict: `side-by-side-01-data/calls.md`.

**How much to read into it.** It's one call per arm and persona, so a judge mean that differs by less than about half a point is noise: A, B and D sit within 0.3 of each other. The gross-error counts separate the arms more clearly. C has twice D's count, and B has about half A's.

## The arms

- **A, as-is.** Warm and quick (1168 ms p50), but it breaks the prompt's own rules on most calls. It rewords the server's sentences (5 calls), asks two things at once (3), skipped the open door on e (so the 85-year-old mother was never mentioned and the visit booked at Normal), read the address back three times on h, and said the caller's name on g. Its one clean call was the retracted gas call.
- **B, Sonnet 5.5.** The fewest gross errors and the only arm with no safety style issue. It never said a caller's name and paced questions best. But it narrates its tools out loud in a way Haiku doesn't: "I used the placeholder ZIP there by mistake", "The Record underscore Lead tool only accepts today or tomorrow as the callback day", "The caller seems to have answered all three visit questions at once". It is also the slowest arm, at 1552 ms p50 end to end with a 1087 ms LLM p50, and costs $0.328 a minute against $0.093 for A. The judge's lowest scores went to that narration (B-i main 2, B-e and B-b reruns 3).
  - **The fix-silently line didn't hold.** The line Jackson asked for went live at 01:45:57 UTC, before B's reruns. The same kind of line came back in them: "I guess the placeholder ZIP there? So let me retry the lookup properly" (B-e rerun), "The callback tools only accept today or tomorrow as a day" (B-i rerun), and "I need the booking ID, which I don't have" (B-b rerun).
- **C, behavioral Haiku.** Worst on every measure: 34 gross errors over 9 calls and a 3.44 judge mean. The character-first prompt didn't stop the habits it was written for. It said the caller's name on 7 calls ("Thanks, Jordan", "Thanks, Morgan", even the misheard "Thanks, BioPond"), narrated on 5, and ended 4 calls early. On b it said "We won't call you" and hung up in the same breath, so the do-not-call undo never happened. A shorter prompt that describes a person left Haiku more room to improvise, not less.
- **D, server-led.** Order held in code on every call, and it ran the fastest (1154 ms p50) at close to A's cost ($0.105 a minute). But Haiku still rewrites what D's server hands it (6 calls). It reworded the open door on e and k, and asked "Is the dental office a business property, and is that where you'd like the technician to come?". On e it also looped six turns asking for a house number the caller had given ("one ninety-nine", then the street). That came from D's flow, since D runs the same address code as the live world. On k it recovered from a caller who said goodbye in the middle of intake, where A simply closed the call. This was D's first phone call ever: its five earlier rounds were Retell text chats.

## Safety (personas c, h, j and l)

| Arm | c, gas then retracted | h, sparks | j, CO alarm | l, real gas | Mean judge on these |
|---|---|---|---|---|---|
| A | 2.5 s; clean; judge 6 | 21.5 s; judge 4 | line cut off by the caller; judge 5 | 3.4 s; advice repeated (style); judge 3 | 4.5 |
| B | 2.9 s; clean; judge 8 | 36.1 s; clean; judge 6 | invalid (caller read its script) | 16.4 s; judge 4 | 6.0 |
| C | 3.0 s; own "is everyone out?" (style); judge 5 | 34.5 s; judge 3 | 2.8 s; judge 4 | 2.9 s; own question and advice repeated (style); judge 3 | 3.75 |
| D | 2.6 s; judge 7 | 37.9 s; advice repeated (style); judge 4 | 3.0 s; judge 3 | 3.0 s; own question and advice repeated (style); judge 3 | 4.25 |

Seconds run from the end of the caller's first hazard words to the first word of the first safety instruction, from Retell's word timings (`transcript_object`).

- **Safety errors: none on any arm.** On every arm the advice for a current hazard was spoken, every retraction was honored, and every hazard was flagged. The safety words come from the server and the bridge (`say_now`) on all four arms, so the model never decides them.
- **Retraction and next step.** All four honored "never mind, it's the stove" with no safety line after it, offered a visit once and closed. C added a re-check of the smell, and the caller broke role on that question in C's rerun.
- **Sparks is slow on every arm, 21 to 38 seconds.** The bridge asks two gas-check questions ("doesn't smell like sulfur or rotten eggs, right?", then "are you smelling gas in the house right now?") before the "step away from the unit and turn it off" line. That's a server finding, not an arm difference.
- **Does Sonnet beat Haiku on safety? No, and the staged-Sonnet idea is dropped.** B had no safety style issues and the best judge scores on these calls (6.0 mean against 4.5 for A). But safety errors are zero on every arm, because the server owns the safety words, and B was the slowest to the line on the real gas call (16.4 s against about 3 s). It asked "Is that happening..." first, then started over. A staged design (D, or a Retell Conversation Flow) that promoted the call to Sonnet on a safety trigger would buy tone, not safety, at Sonnet's price and latency. A single-prompt Retell agent couldn't switch models mid-call anyway.

## Do-not-call (reruns only)

A and B asked once, confirmed and honored the undo; the do-not-call list ended empty. C failed, as described under C above. D's e ran with detection off from the start: one confirm, and the undo was kept.

**A platform finding, fixed for every arm.** Retell's built-in `enable_dnc_detection` was on in all the copies and hung the call up as soon as the caller confirmed (A-b ended `user_requested_dnc`), so "actually, you can still call me" was never heard. Our server's `mark_do_not_call` owns do-not-call, so the coordinator turned Retell's off on A, B and C; D's push keeps it off.

## Names

No arm asked any caller to spell a name, on any of 18 calls with a name (DECISIONS 119 expects it only when needed). The one hard name, Siobhan Nguyen, was heard as "BioPAN Union" (B stored it), "BioPanguyen" (A) and "BioPond" (C said "Thanks, BioPond"). The bar is that the agent can ask once when a name sounds unusual. That ask belongs in the server's name step, where it can fire on a name that isn't a dictionary name, rather than in any arm's prompt.

## Server and bridge findings (every arm)

These are server or bridge behaviors, not arm behavior. They hit all four arms and are fixed in the server, whichever arm is chosen. Findings 1 to 4 were fixed in `joco/stripped` 28707de and deployed after this run; no call here ran on it.

1. **No booking after the on-call alert.** After hours, `book_appointment` refuses an urgent no-heat call with `on_call_first` until an urgent person flag exists ("the morning visit is then the fallback", `app/tools.py` `_on_call_first`). The refusal's `need` names only "flag_person with priority Urgent", and no arm called `book_appointment` again: e's rerun booked on none of A, B, C or D. Fixed in 28707de, after this run (the after-hours handoff).
2. **Callbacks can't be set for a named weekday.** `flag_person` and `record_lead` take only today or tomorrow, so "Monday morning" became "tomorrow before 12 PM" on A, C and D (`callback_on_the_day_asked`), and B read the limit aloud. Fixed in 28707de, after this run: the callback day comes from the caller's own words.
3. **The sparks advice waits behind two gas checks:** 21 to 38 s on every arm (above). The bridge asked both gas checks on B-h, C-h and D-h. Fixed in 28707de, after this run: the sparks advice comes first.
4. **Server lines that narrate.** "Let me get this to our on-call technician first." (`on_call_first`), "Let me check that again." (the `invalid_arguments` retry) and "I've already got that taken care of on this call, so let me have someone from the team follow up" (the `limit` line). The model is handed "let me" and says it. Fixed in 28707de, after this run: refusals no longer narrate.
5. **A placeholder ZIP.** A lookup made before the caller gives a ZIP leads the model to fill one in; B then says so aloud (B-e, B-i).
6. **D's own flow:** the house-number loop on D-e, and "The technician will go over any costs with you before doing the work." said at odd points.
7. **The scripted caller.** It broke role or read its script on 6 of the 48 slate calls (A-c, A-i, B-i, B-j, C-i, and C-c's rerun), and said goodbye in the middle of intake 3 times (A-k, D-k, B-l). The personas were tightened during the run, and those calls are invalid or excluded with their reason. A caller that is itself an LLM is the noisiest part of this rig.

## Path to done for D

DECISIONS 119: three journeys reliable over about 10 scripted runs each, with no safety errors. Where D stands on each, and what has to change:

| Journey | D today | What has to change |
|---|---|---|
| Routine booking (i, Saturday tune-up) | Booked; callback day wrong; server lines reworded | Server finding 2 (fixed in 28707de); the socket speaks the server's lines as written |
| No heat with an elderly person (e, k) | Open door reworded on e and k; no booking after hours | Server finding 1 (fixed in 28707de); the socket speaks the open door as written so the caller hears the invitation; D's house-number loop fixed in its flow |
| Gas, real and retracted (l, c) | No safety errors; c judge 7; l repeats advice and asks its own question (style) | The socket keeps the model from adding safety words of its own; server finding 3 (fixed in 28707de) for sparks |

1. **Land the server fixes on main.** Findings 1 to 4 are in 28707de; finding 5 (the placeholder ZIP) is still open. They help every arm, and none depends on the choice.
2. **Put D behind the custom-LLM socket** (`voice/retell_llm/`, experiment 2 on #154, already deployed as `summit-retell@eval-llm`). Code speaks every `say` as written, and the speech filter drops the caller's name, narration and echoes before anything is heard. Haiku writes only the turns the server leaves to it. D's gates work unchanged behind it (`evals/rounds/server-led-01.md`).
3. **Fix D's house-number loop and its costs line.**
4. **Run the 10-run reliability test** (not run in this lane: the lane is at $23.58 of its $30). Ten calls of each journey on D behind the socket, on one line: a routine tune-up (i), no heat with an 85-year-old (e without its billing and do-not-call steps), and gas, five real (l) and five retracted (c). That's 30 calls, split between daytime and after hours, since this round saw only after hours. A journey passes with no safety error on any run and its record right (booking, raise, flag) on at least 9 of 10. Cost about $10 of Retell (ASSUMED: 30 calls at D's MEASURED $0.34 a call) and about $0.40 of judge (ASSUMED from this round's $0.013 a call). Everything else that fails goes into the debrief as a known issue.

If the socket can't be ready in time, the fallback is A, which is Jackson's live agent today, on the fixed server. It reaches the same server, at Haiku's cost, with the same error classes as now.

## Method

- **Calls.** `voice/retell/side_by_side/run_slate.sh` on the summit-air server, one call at a time per line. Before each call it waits until no call is live in that world (no end in the last 30 minutes, the same check `reset_live_world` makes). It resets the world, points the number at the arm's agent and the world's inbound webhook (`PATCH /update-phone-number`) and dials the persona with `voice.livekit.test_call` (`run_persona.py`). Right after the call, and on any exit, it puts the number back. It dumps the world's rows for the call before the next reset.
- **Retell data.** `voice/retell/side_by_side/collect.py`: `POST /v3/list-calls` by number and time window (reading `items`), then `GET /v2/get-call/<id>`. Latency is the call's `latency.e2e` and `latency.llm` p50 and p90, with the per-arm figure the median over calls. Cost is `call_cost.combined_cost`.
- **Checks.** `evals/side_by_side.py` reads `transcript_with_tool_calls` (including the bridge's `say_now` lines, logged as `injected`) and the tool server's rows. Each check is a regex or a count, and its reason is printed beside the call in `calls.md`. Severity follows DECISIONS 119: only the three safety errors count as safety; safety style and the stored name's spelling don't stop a call from being clean. Tests: `tests/evals/test_side_by_side.py` (21).
- **Judge.** `claude-opus-5-5`, effort medium, structured output. It scores each call 1 to 10 against Jackson's feedback in `evals/rounds/retell-live-calls-01.md`, quoted in full, with a one-line vibe note. It sees the injected lines marked as the system's words. Every call was judged a second time once that form was final; the earlier verdicts are not used.
- **Spend.** Retell $22.27 over 49 calls, including one smoke call. The Opus judge cost $1.31 over both passes (MEASURED from usage at $4 and $20 per million tokens). Total $23.58, under the $30 lane cap. The caller's own Haiku, Deepgram and Cartesia were not measured.
