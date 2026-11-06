# STT round 1: Cartesia Ink-2 against Deepgram Nova-3

Should our ears change from Deepgram Nova-3 to Cartesia Ink-2, or should Ink-2 join the bake-off? Every number here is MEASURED by the STT bench (`voice/archive/livekit/stt_bench.py`, `make stt-bench`) in one run, results file `evals/rounds/stt-ink2-01-data/20261104-110914-ink-2.json`, unless it says otherwise. Exploration 014, idea 7, covers the neighbouring option, Deepgram Flux.

**In short: keep Deepgram.** Ink-2 was slower on every turn and worse on local street names, and tied on digits and spelled names. Its final transcript came a median 1.08 s after the caller's last word against 0.44 s for Nova-3, and LiveKit cannot use Ink-2 without waiting for that final, so a caller would wait about 0.6 s longer on each turn. With the same 15 keyterms, Ink-2 wrote "Fairfield Street" for Duffield and "Cortalejo Road" for Cortelyou (a keyterm) on one of the two voices, 28 of 32 street spans against Nova-3's 32 of 32. On safety the arms split one miss each, both on one clip in one voice. One of those is a real gap in our current stack: Nova-3 wrote "c o alarm", and the tool server's safety screen does not catch that spelling (section 5).

## 1. What ran

**The audio.** 18 scripted caller turns (8 street names and addresses, 4 ZIPs and phone numbers said in digit groups with pauses, 2 spelled names, 4 safety lines), each rendered once in two caller voices by the rig's own voice (Deepgram Aura 2: `aura-2-thalia-en` and `aura-2-orion-en`, `audio_rig.stt_bench` in config/models.yaml), so 36 distinct clips. Each clip is 8 kHz, put through a G.711 mu-law round trip with line noise at -60 dBFS (ASSUMED level), with 0.5 s of silence before and 6 s after. The 6 s outlasts Ink-2's longest turn wait, 5.6 s by default (SOURCE: the LiveKit Cartesia plugin's STT docstring). Street and digits turns ran twice per voice, the rest once: 60 runs per arm.

**The arms.** Both through the LiveKit plugins the worker uses, built by the worker's own `build_stt` with the tenant's 15 local names as keyterms (tenants/summit-air/names.yaml), so each arm is configured exactly as a call would have it:
- Nova-3: `deepgram.STT`, `endpointing_ms` 100, the plugin's other defaults.
- Ink-2: `cartesia.STT`, model `ink-2`, the plugin's and Cartesia's turn defaults. Keyterms within Cartesia's limit of 100 terms and 1,200 characters (SOURCE: https://docs.cartesia.ai/use-the-api/stt/keyterms.md).

Both arms got the same 20 ms frame at the same moment, in real time, so network and load were shared. Nothing else of the stack ran: no VAD, no turn detector, no model.

**Which turn detection.** The installed plugin (livekit-plugins-cartesia 1.8.10) exposes Ink-2's own turn detection, and it sends a final transcript only when Ink-2 ends the turn (`AutoFinalizeRecognizeStream`; LiveKit's flush is ignored). So keeping our turn detector over Ink-2 would still wait for Ink-2's turn end, and the comparison is necessarily Ink-2 with its own turn detection against Nova-3 as an STT only. With Nova-3, LiveKit's turn detector and our end-of-turn wait decide the turn; this bench does not run them, and their measured cost is quoted from round 2 of the bake-off.

**Scoring.** Each turn has key spans that must come out exactly, after a normalization that reads numbers the way they are read back: punctuation dropped, number words turned into digits and adjacent numbers joined, so "two forty eight", "2 48" and "248" all count as 248, and "718-555-0142" as 7185550142 (`normalize`). A spelled name counts only as letters said one at a time, not the name said whole. Safety turns are also run through the tool server's own phrase screen (`app/safety.py`), since that, not the span, decides the safety script.

## 2. Word accuracy on the key spans

Hits over spans; n is runs, with 36 distinct clips behind them.

| Group | Runs per arm | Nova-3 | Ink-2 |
|---|---|---|---|
| Street names and addresses | 32 | 32 | 28 |
| ZIPs and phone numbers | 16 | 16 | 16 |
| Spelled names | 4 | 3 | 3 |
| Safety phrase, exact span | 8 | 8 | 7 |
| Safety phrase, server screen finds the right kind | 8 | 7 | 8 |

The misses, every one listed:
- Ink-2, orion voice, both runs: "248 **Fairfield** Street" for Duffield (not a keyterm).
- Ink-2, orion voice, both runs: "**Cortalejo** Road" for Cortelyou Road, although "Cortelyou Road" was a keyterm.
- Both arms, thalia voice: Siobhan's spelling came out with an F for the S ("F I O B H A N" from Ink-2, "F I o b. H a N" from Nova-3). Same clip, same miss, so most likely the synthesized voice; a tie either way.
- Ink-2, orion voice: "I smell**ed** gas". The exact span misses, but the server screen still reads it as gas, so no harm on a call.
- Nova-3, orion voice: "The **c o** alarm is going off". The span counts it, but the server screen does not (section 5). The same clip gave the same transcript in the smoke run before the main run.

Ink-2 wrote digits as numerals ("718-555-0142") and Nova-3 as words ("seven one eight five five five"); after normalization both were exact on all 16 digit runs. Repeats of a clip gave the same transcript every time on both arms, so the repeats show the services' timing spread, not more accuracy evidence: the street result is two clips (Duffield and Cortelyou, orion voice), each missed twice.

## 3. End-of-turn timing and cut-offs

Seconds from the caller's last word (the end of the last voiced frame) to the event, 60 runs per arm.

| Measure | Nova-3 | Ink-2 |
|---|---|---|
| Last final transcript, median | 0.44 s | 1.08 s |
| Last final transcript, slowest 10% | 0.50 s | 1.66 s |
| Last final transcript, slowest | 0.53 s | 2.07 s |
| Ink-2's early "might be done" event (`turn.eager_end`), median | n/a | 0.70 s |
| Runs with no final transcript | 0 | 0 |
| Runs where the turn ended before the caller's last word | not decided by the STT (see below) | 4 of 60, all on one digits turn |
| Turn ends before the caller's last word | | 6 |

By group, Ink-2's median final was 1.01 to 1.10 s and Nova-3's 0.39 to 0.46 s; neither arm was faster on any group.

**What the caller would wait.** On Nova-3, our stack ends a plain turn a median 353 ms after the caller stops, with Deepgram's final at about 0.3 s inside that (MEASURED, evals/rounds/bakeoff-audio-02.md, from LiveKit's own stage times; this bench's 0.44 s includes the plugin's chunking and the last of several finals). On Ink-2, LiveKit's turn cannot end before Ink-2's final, so the turn ends at the earliest 1.08 s, about 0.6 to 0.7 s later on every caller turn. Even Ink-2's early guess (0.70 s), which LiveKit uses for its early reply draft, comes after Nova-3's final. Ink-2's turn wait has thresholds (`turn_end_threshold`, `turn_end_timeout_ms`); this round tried only the defaults, and lowering the wait would trade against the cut-offs below.

**Cut-offs.** Ink-2 ended the turn mid-number on all 4 runs of the slow phone number (pauses of 1.5 s between groups) and never on the 0.8 s or 0.7 s pauses. Every run ended after "nine one seven", 0.37 to 0.59 s before the caller went on; on the orion voice it also ended after "four four two", and that end arrived when the caller had already started the last group. In LiveKit's "stt" turn mode our digits wait still applies (at least 0.9 s, counted from the end of speech, `livekit/agents/voice/audio_recognition.py`), so the first four ends would probably have been held, and whether the two that landed mid-speech would have committed the turn is not clear from the code. Both are read from the code, not measured. Nova-3 sent an end-of-speech event before the caller's last word 71 times over 37 runs, which is how Deepgram's 100 ms endpointing works; on our stack LiveKit's turn detector and the digits wait decide the turn, and those were measured in round 2 of the bake-off, not here.

## 4. Cost per call minute

| Arm | Per minute | Basis |
|---|---|---|
| Nova-3 with keyterms | $0.0090 | $0.0077 streaming plus $0.0013 keyterm add-on, pay as you go (SOURCE: https://deepgram.com/pricing, read 2026-11-04) |
| Ink-2 at our current overage | $0.0117 | 3 credits per second of audio (SOURCE: https://cartesia.ai/pricing, read 2026-11-04) at $65 per 1M credits, the Pro overage we pay now |
| Ink-2 on the Startup plan | $0.0071 | Same 3 credits a second at $49 for 1.25M included credits (SOURCE: same page); $0.0081 once past them at $45 per 1M |

On a 3-minute call (ASSUMED length) that is $0.027 for Nova-3 against $0.035 for Ink-2 at today's rates. Ink-2 is cheaper only on a Cartesia plan we do not have, and the saving would be under a cent a call.

While checking this, `audio_rig.livekit.stt_per_minute` in config/models.yaml was $0.0077, which left out the keyterm add-on our worker uses; it is now $0.0090, so rig call costs read slightly higher from here on.

**This lane's spend** (MEASURED from audio seconds sent): Cartesia Ink-2 $0.13 (11.1 minutes, about 2,000 credits, at $65 per 1M), Deepgram Nova-3 $0.10, Deepgram Aura for the caller voice $0.05 (1,372 characters in the main run, rendered once and cached in `data/stt_bench/`). About $0.28 all told, inside the $3 cap. The project's earlier Cartesia spend is not visible from here; this lane added $0.13 to it.

## 5. The CO alarm the screen misses

On the orion voice, Nova-3 wrote "The c o alarm is going off, and it won't stop." `app/safety.py`'s `clear_signal` returns nothing for that line, though it returns `co` for "The CO alarm is going off". So on our stack today, a caller whose "C-O" Deepgram writes as two letters would not get the safety script from the server's screen (the model might still raise it; that is not a gate). One clip, seen twice. This is not an Ink-2 point, and it is not fixed here: the fix belongs in the safety phrase list or its normalization, with tests, in its own change.

## 6. Verdict

Keep Deepgram Nova-3, and do not add Ink-2 to the bake-off. The bar was "clearly better on the key spans or timing with no regression on safety phrases or digits". Ink-2 was worse on both measures that differed (about 0.6 s slower to a final transcript on every turn; 28 of 32 street spans against 32 of 32), tied on digits and spelled names, and split the safety turns one miss each.

What would change this: a later Ink-2 or plugin that sends a final transcript before its own turn end (so LiveKit's detector could end the turn sooner), or real-caller audio on which Nova-3 misses local names that Ink-2 gets. The bench reruns either for about $0.28 (`make stt-bench`), and `RIG_VOICE='{"stt": {"provider": "cartesia", "model": "ink-2", "price_per_minute": 0.0117}}' make rig STACK=livekit` puts Ink-2 on the full rig's worker; that full-rig path is covered by unit tests only and was not run.

## 7. Limits

- Synthesized voices, not callers. Aura 2 speaks evenly and clearly; real phone callers mumble, talk over noise and pause in odd places. Both arms heard the same audio, so the comparison is fair, but the absolute accuracy is flattering to both.
- Small n. 36 distinct clips; the street difference rests on two of them, both in one voice. A difference this size on this n is a lean, not proof. It is the timing, the same on every one of the 60 runs, that settles the verdict.
- `make check` ran on the laptop for part of the run. Both arms shared that load, frame for frame.
