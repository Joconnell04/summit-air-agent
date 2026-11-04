# Audio bake-off, round 2: our stack's end-of-turn wait

Follow-up to round 1 (evals/rounds/bakeoff-audio-01.md, proposal 2): could shorter turn-taking values close our stack's gap to the 1.2 s plain-turn target (spec S-36)? Every number here is MEASURED on the simulated audio rig (`evals/audio_rig/`, evals/README.md "Audio rig") unless it says otherwise.

**In short.** The end-of-turn wait is not where our time goes, and the settings stay as they are (0.35 s of silence). Shortening the silence wait to 0.25 s, the least LiveKit's turn detector accepts, cut the wait it controls by 39 ms (median 355 to 316 ms) and the caller-heard plain median did not improve (1.97 s to 2.25 s, run-to-run noise that size). The runs did find a real bug: after the agent asked for a ZIP or phone number, the longer digits wait stayed on for the rest of the call, because LiveKit writes option changes into the dict the worker handed it. On the one rig scenario that asks for digits, all 13 caller turns after the ZIP answer waited 0.9 s or 3.0 s instead of 0.35 s. That is fixed (voice/livekit/call.py, voice/livekit/worker.py), and on a rerun the wait went back to 352 ms after the digits answer, saving about 0.55 s on each of those turns. Across the whole suite the plain median didn't move beyond noise (1.97 s to 2.02 s), because only one of the eight smoke scenarios asks for digits; on a live booking call, which asks for a ZIP or phone number early, it applies to most of the call.

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

**Where the time goes instead (plain turns, baseline).** The worker's own `reply_ms`, from LiveKit deciding the caller stopped to the agent's first audio, had a median of 1.34 s. Of that, the model's first token was 0.52 s and the voice's first byte 0.18 s; the remaining 0.64 s is not covered by any stage timing yet. It includes the caller's line going to the tool server and back before the model is asked (voice/livekit/call.py `caller_line` waits for the server's answer, which carries the safety check and the priority gate), and LiveKit handing the reply to the voice. The rig read the plain-turn delay a median of 0.64 s above `reply_ms` in this session (0.55 s on round 1's file), more than the 0.39 s round 1 reported, so the stretch before `reply_ms` starts (the silence wait plus the audio's trip to the worker on LiveKit Cloud and back) is about 0.3 s more than the silence wait itself here.

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
3. Time the caller line's trip to the tool server as a stage (`server_ms`) before the real calls, since the 0.64 s after the turn ends that no stage covers is now the biggest unexplained piece.
4. Add "just me", "a cane" and similar to the priority phrase lists, through the round that owns them.

## 7. Cost

| Run | Cost |
|---|---|
| Baseline, 0.35 s, 8 calls | $0.71 |
| 0.25 s, 8 calls | $0.73 |
| Fixed worker, 8 calls | $0.63 |

All MEASURED as the sum of `cost_usd` in each results file, rig parts included. $2.07 in all, about the $1 to $2 approved; no run passed its $1 cap.
