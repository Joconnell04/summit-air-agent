# Audio bake-off, round 2: our stack's end-of-turn wait

Follow-up to round 1 (evals/rounds/bakeoff-audio-01.md, proposal 2): could shorter turn-taking values close our stack's gap to the 1.2 s plain-turn target (spec S-36)? Every number here is MEASURED on the simulated audio rig (`evals/audio_rig/`, evals/README.md "Audio rig") unless it says otherwise.

**In short.** The end-of-turn wait is not where our time goes, and the settings stay as they are (0.35 s of silence). Shortening the silence wait to 0.25 s, the least LiveKit's turn detector accepts, cut the wait it controls by 39 ms (median 355 to 316 ms) and the caller-heard plain median did not improve (1.97 s to 2.25 s, run-to-run noise that size). The runs did find a real bug: after the agent asked for a ZIP or phone number, the longer digits wait stayed on for the rest of the call, because LiveKit writes option changes into the dict the worker handed it. On the one rig scenario that asks for digits, all 13 caller turns after the ZIP answer waited 0.9 s or 3.0 s instead of 0.35 s. That is fixed (voice/archive/livekit/call.py, voice/archive/livekit/worker.py), and on a rerun the wait went back to 352 ms after the digits answer, saving about 0.55 s on each of those turns. Across the whole suite the plain median didn't move beyond noise (1.97 s to 2.02 s), because only one of the eight smoke scenarios asks for digits; on a live booking call, which asks for a ZIP or phone number early, it applies to most of the call.

## 1. What ran

The smoke suite, each of the 8 scenarios once (the two must-pass safety scenarios included), on our LiveKit stack only, one run at a time on the laptop, all in the same session so the baseline and the trials share network and load. Three runs:

| Run label | Silence wait (`voice.vad.min_silence_s`) | Normal `min_delay` | Worker | Results file |
|---|---|---|---|---|
| `livekit-rig` (baseline) | 0.35 s | 0.3 s | as on main | `evals/results/20261104-040405-livekit-rig-fast-smoke.json` |
| `livekit-rig-s025` | 0.25 s | 0.2 s | as on main | `evals/results/20261104-042832-livekit-rig-s025-fast-smoke.json` |
| `livekit-rig-fixed` | 0.35 s | 0.3 s | digits wait fixed | `evals/results/20261104-055115-livekit-rig-fixed-fast-smoke.json` |

The digits wait (`voice.endpointing.digits`, 0.9 s minimum, C-48) was left as it is in every run.

The rig now records two things it didn't in round 1 (evals/audio_rig/line.py, rig.py): every stage timing the worker logged for the caller's line and its reply (`platform_stages`, including LiveKit's own `end_of_turn_ms` and `transcript_ms`), and whether the agent made any sound while the caller was still speaking (`over_caller_ms`). Our stack never backchannels, so sound over the caller is the agent cutting someone off mid-line; a shorter wait can't look fast by doing that unnoticed. It also counts talk-overs a little more widely than in round 1 (section 4).

## 2. Results

Reply delay is from the end of the caller's speech to the agent's first sound, as the caller hears it (round 1, section 2). "End of turn" is LiveKit's own measure of how long it waited after the caller's speech ended before deciding the turn was over, over every turn of the run.

| Run | Plain median | Plain slowest 10% | Lookup median | Lookup slowest 10% | End of turn, median | Talked over / sound over caller / silent | Must-pass code checks | All code checks | Cost |
|---|---|---|---|---|---|---|---|---|---|
| Baseline, 0.35 s | 1.97 s (25) | 2.66 s | 2.35 s (24) | 3.23 s | 355 ms (48) | 0 / 0 / 0 | 6 of 6 | 8 of 8 | $0.71 |
| 0.25 s | 2.25 s (23) | 3.04 s | 2.26 s (25) | 4.48 s | 316 ms (44) | 0 / 0 / 0 | 6 of 6 | 7 of 8 | $0.73 |
| Fixed worker, 0.35 s | 2.02 s (21) | 2.55 s | 2.16 s (22) | 3.13 s | 353 ms (42) | 1 / 1 / 0 | 6 of 6 | 8 of 8 | $0.63 |

Both safety calls in every run had the server's check pass and the signed-off lines heard in full (95 to 100% of the words).

**Why 0.25 s barely helps.** Three things bound the normal wait, and the silence setting is only one:
- LiveKit counts `min_delay` from the end of speech, not from the end of the silence wait (`livekit/agents/voice/audio_recognition.py`, `extra_sleep`), so a normal `min_delay` of 0.3 s sits inside the 0.35 s silence wait and adds nothing. The silence wait alone sets the normal wait.
- LiveKit's turn detector refuses a silence wait under 0.25 s (`MIN_SILENCE_DURATION_MS` of 200 ms plus 50, raised when the session starts, so on a call).
- The turn can't end before Deepgram's final transcript, which came a median of 304 ms after speech ended (274 ms in the second run). At 0.25 s the transcript, not the silence, is what the turn waits for.

So 0.35 s to 0.25 s can save at most about 0.1 s, and saved 39 ms. Detecting a change that size in the caller-heard median would take hundreds of calls; the per-turn spread is several hundred ms, and the plain median of the same settings moved from 1.89 s in round 1 to 1.97 s here. The stage timing settles it instead.

**Where the time goes instead.** See section 7: with every stage logged, a plain turn's 1.30 s inside the worker is the 0.35 s end-of-turn wait, 14 ms to the tool server, 0.71 s to the model's first sentence and 0.18 s to the voice's first byte. The model is the biggest piece; the wait is the second.

**The turn detector's long wait.** On about one turn in seven (6 of 48, 7 of 44 and 7 of 42), LiveKit's turn detector judged the caller not finished and waited the 1.5 s maximum (`voice.endpointing.normal.max_delay`). The 13 in the first two runs were all complete lines, most of them ending in an address, a ZIP or "the number I'm calling from", so each cost about 1.15 s for nothing. That maximum is the turn-taking value with real time in it. It was not trialled: a shorter one is what cuts off a real caller who pauses mid-thought for a second, and the rig's caller voice never pauses like that, so the rig can't say what a lower value would cost.

## 3. The digits wait that stayed on

`elderly-no-heat` is the one smoke scenario where the agent asks for a ZIP. In the baseline, the end-of-turn wait read 352 ms on the two caller turns before the question and 900 ms on the ZIP answer, as intended, then 900 ms (once 3,000 ms) on all 13 caller turns after it, though none of the agent's later lines asked for digits. The 0.25 s run showed the same pattern.

Cause: the worker built LiveKit's session with its own `voice.endpointing.normal` dict. LiveKit keeps that dict as the session's endpointing overrides and writes later `update_options` values into it, so switching to the digits wait overwrote `normal` in the worker's memory, and switching back set the digits values again. Reproduced offline on a real `AgentSession`; the session and every update now get a copy, with a test that fails on the old code (`tests/voice/test_voice_call.py`, `test_the_normal_wait_comes_back_after_the_digits_on_a_real_livekit_session`).

On a live booking call the agent asks for a ZIP or a number early, so most of every booking call has been running on the 0.9 s wait: about 0.55 s more per caller turn than set. The first real call's 2.7 s plain median (docs/latency.md) likely carries some of this.

On the rerun with the fix, the same scenario's waits read 352 ms on the turns before the question, 900 ms on the ZIP answer, then 352 ms, 1,500 ms (the turn detector, section 2) and 352 ms after it (MEASURED, `livekit-rig-fixed`). Each turn after a digits question now waits about 0.55 s less (900 ms to 352 ms).

## 4. Cut-offs

The first two runs had no reply over the caller and no silent turn. The fixed-worker run, at the same 0.35 s as the baseline, had one: in `out-of-area-known-caller` the caller's 13-second goodbye ("...thanks for checking for me. I'll look up someone local. Have a good day.") had a pause between sentences, the worker took it as the end of the turn, and its own goodbye started 1.7 s before the caller finished. The setting there was the one in production, so this is a risk the current wait already carries on long lines, not one a shorter wait adds; with one case in 141 timed turns across the three runs, the rig can't say whether 0.25 s would make it more common.

That reply reached the caller 0.3 ms after the end of the caller's line by the rig's clock, which the report counted as a fast reply rather than a talk-over, since it only treated exactly zero as one. The report now counts any reply within one 20 ms frame of the caller's end as a talk-over (evals/audio_rig/report.py `TALK_OVER_MS`). Rerun on round 1's files, that changes round 1's talked-over counts from 1 to 4 for our stack and from 5 to 7 for OpenAI Realtime; the plain medians move by 0.01 s at most (ours 1.89 s to 1.90 s) and the round 1 verdict stands. The rig's caller voice reads ZIPs and phone numbers fluently, without the pauses between digit groups that real callers make, so the rig can't show whether a shorter wait cuts off a real caller reading digits; the digits wait is counted from the end of speech and doesn't depend on the silence setting, so it is the same 0.9 s whichever silence wait is used.

## 5. The one failed check, and why it isn't the wait

In the 0.25 s run, `elderly-no-heat` (not must-pass) booked at High where the scenario expects Urgent. The caller said "It's just me at home. I'm 84, and I use a cane" in one line, heard whole (no sound over the caller). In the baseline the same caller said "I'm 84, and I live alone", which matches the `lives_alone` phrase list (trades/hvac/agent.yaml); "just me at home" and "a cane" match no phrase, so only the age counted. That is a gap in the priority phrase lists, not turn-taking. The agent also told the caller "I've marked this as one of our most urgent calls" while the server had it at High.

## 6. Decision

The default stays at 0.35 s silence and 0.3 s normal `min_delay` (config/models.yaml, comments updated to say why). The fix to the digits wait is the change. Proposals for Jackson (not decided):
1. Keep 0.35 s: 0.25 s is the floor, saves under 0.1 s by construction, and leaves less room for a real caller's pause, which the rig can't test.
2. Try a lower turn-detector maximum (`voice.endpointing.normal.max_delay`, 1.5 s now) on real calls, not the rig: it wasted about 1.15 s on one turn in seven here, and only real callers' pauses can show what a lower one cuts off.
3. Add "just me", "a cane" and similar to the priority phrase lists, through the round that owns them.

## 7. The worker's reply timer, and two voice-side trials

A second batch, after main's round 3 changes were merged in (exploration 014, ideas 1 and 8): fix the worker's own reply timer on tool turns, keep its stage times in the rig results, and try two voice-side settings one at a time against a fresh baseline on the same code.

**The reply timer.** The worker's `reply_ms` was stamped on every agent line, timed from the caller's end, so on a tool turn where the agent spoke twice ("Let me check", then the answer) the second line read seconds high, and the rig paired a turn with its newest line. Now only the first agent line after a caller line carries `reply_ms`, a bridge clip that played first counts as the first audio, and the rig takes a turn's stages from its first line. On tool turns, lines where `reply_ms` read more than 0.5 s above what the rig heard went from 24 of 39 (worst 8.7 s; round 1's two files) to 3 of 37 (worst 1.9 s; `livekit-rig-base2` and `livekit-rig-ptts`). The three left are sign-off turns, where the goodbye spoken from code after `end_call` is the first line the worker logs.

The timer did not need the silence wait added. LiveKit's `stopped_speaking_at` is the end of the caller's speech, not the end of the silence wait: its own `end_of_turn_ms`, timed from the same moment, reads the 0.35 s wait (353 ms median). Round 1 (section 5) said the wait was left out; it wasn't. The 0.58 s the rig reads above `reply_ms` on plain turns is the audio's path to the worker on LiveKit Cloud and back, from a laptop.

**Stage times in the rig results.** Each turn's `platform_stages` now holds the worker's end-of-turn wait, transcript delay, `server_ms` (the caller line's trip to the tool server before the model can answer, new), the model's first token, tool time and the voice's first byte. Plain turns in the fresh baseline, medians (15 turns): end of turn 353 ms, server 14 ms, model 706 ms, voice first byte 176 ms, `reply_ms` 1,302 ms; the parts add up to within about 50 ms. The model figure is the time to its first whole sentence, since the worker passes the reply on a sentence at a time (voice/archive/livekit/call.py `llm_node`).

**The trials.** Each is one 8-call smoke run, with the change set through `RIG_VOICE` and both new settings in config/models.yaml at today's values.

| Run | Change | Plain median | Plain slowest 10% | Lookup median | Talked over / sound over caller / silent | Must-pass code checks | Read-back parts heard | Model first sentence | Voice first byte | `reply_ms`, plain | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `livekit-rig-base2` | none (today's values) | 1.94 s (16) | 3.27 s | 2.30 s (20) | 0 / 0 / 1 | 5 of 6 | 18 of 18 | 706 ms | 176 ms | 1,302 ms | $0.61 |
| `livekit-rig-ptts` | `preemptive_tts: true` | 2.22 s (15) | 3.62 s | 2.27 s (18) | 3 / 4 / 3 | 5 of 6 | 15 of 15 | 787 ms | 170 ms | 1,604 ms | $0.58 |

"Read-back parts heard": for every `read_back` the model called, whether the ZIP, the last four of the phone number and the house number were all in what the rig heard the agent say (MEASURED, digits matched in the rig's transcription). The one must-pass miss in the baseline and the preemptive run is `tune-up-known-caller` failing round 3's new `booking_lead_in` check, at today's values in the baseline, so it is not from either change.

- **Preemptive voice: not kept.** The plain median rose 0.28 s, the worker's own `reply_ms` rose 0.30 s, and it started talking over callers: 3 replies over the end of the caller's line, 4 turns with sound while the caller was still speaking, and 3 silent turns (none in the baseline). In the preemptive run's tune-up call no booking was made at all. A voice started on a draft before the turn is confirmed is the cut-off risk this file set out to avoid.
- **A shorter first sentence (`tts.min_sentence_chars` 20 to 5): not run to a result, so not kept.** The first attempt stopped after two calls when the laptop went to sleep on battery and lost its network (no results file); the laptop kept sleeping after that, so it was not retried. The setting is in config/models.yaml at today's 20, and one command runs the same 8 calls: `RIG_VOICE='{"tts": {"min_sentence_chars": 5}}' RIG_LABEL=sent5 uv run --env-file .env python -m evals.audio_rig --stack livekit --suite smoke --repeats 1`. It can only help when a reply opens with a sentence under 20 characters ("Got it."): the voice then starts on it instead of waiting for the next sentence. The worker already hands the voice whole sentences, so it changes nothing on longer openers.

## 8. Cost

| Run | Cost |
|---|---|
| Baseline, 0.35 s, 8 calls | $0.71 |
| 0.25 s, 8 calls | $0.73 |
| Fixed worker, 8 calls | $0.63 |
| Second baseline, 8 calls | $0.61 |
| Preemptive voice, 8 calls | $0.58 |
| Short first sentence, stopped after 2 calls when the laptop slept (no results file) | $0.09 (MEASURED from the run log) |

All MEASURED as the sum of `cost_usd` in each results file, rig parts included. The first batch (three runs) was $2.07; the second (the timer fix and voice trials) $1.28 against about $2 approved. No run passed its $1 cap.
