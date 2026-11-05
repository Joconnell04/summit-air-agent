# Live latency, round 1: the wait on live calls 1 and 2 (issue #150)

Jackson's first two live calls on the LiveKit stack felt slow and robotic. This lane measured where the wait went and fixed what it could, one change at a time, on the audio rig. On 2026-11-05 the project moved to Retell, starting from Jackson's own agent, and the LiveKit custom stack stopped. This note is the record of what was measured. Every number is MEASURED on the audio rig (evals/audio_rig) or offline, as stated; results files are in evals/results under the run labels below.

**In short.** The biggest waste was structural, not the model: every early draft LiveKit started was thrown away, the reply filter held whole replies until the model finished, and the turn detector waited its full 1.5 s on about a quarter of caller turns. Turning the drafts off and narrowing the hold are on joco/stripped (PR #160); 0.9 s for the long wait measured better than 1.5 s with no extra cut-offs but was never shipped. Flux was not faster. Prompt size was not a lever.

## What each arm measured

Rig runs on summit-rig (8 cores) unless marked Mac. Calls are simulated callers on the realism lane's live-like suite (PR #157) or the rig's own scenarios, with a 0.8 s pause inside long caller lines where noted.

| Label | What changed | Result |
|---|---|---|
| `main15` (Mac and box) | main, long wait 1.5 s, 8 calls | plain 2.21 s median / 3.11 s slowest 10%, lookup 2.43 / 4.17 s; 15 of 56 caller turns hit the 1.5 s wait. Box and Mac agree within 0.12 s on the same calls |
| `e2` | #160 + Flux (Deepgram STTv2, turn decided by the STT) | plain 2.78 / 3.64 s, lookup 2.64 / 3.59 s; Flux ended the turn inside a 4 s mid-sentence pause (its eot_timeout_ms default is 3000) |
| `e1` | #160, Nova-3 (same four calls as e2) | plain 2.03 / 2.60 s, lookup 2.58 / 3.79 s; Nova picked over Flux |
| `hr` | the dead-end follow-up fix (joco/stripped 4b160eb) | the "Got it, 248 Duffield Street." then 21 s of silence was gone; two other half replies remained (choices offered with no question), fixed later by a prompt line |
| `ackA` / `ackB` | interruption backchannel_boundary 1.0 s vs 0 at the start | no change the rig could show; its caller never speaks over the agent, so the live call is the test |
| `eot15` / `eot09` | long wait 1.5 s vs 0.9 s on joco/stripped (d1902ed), four live-like calls each | lookup 2.62 / 4.75 s vs 2.06 / 2.78 s; plain 2.03 / 3.36 s vs 2.04 / 2.46 s; talk-overs in 3 vs 2 calls; silence in 2 vs 0 |
| `quiet` | live-call3-one-word-quiet on joco/stripped (with the question-ending prompt line) | half replies 5 (on 5b57b92) to 0; quiet one-word answers were always heard (transcripts in 285 to 375 ms); what failed was replies that asked nothing |

Offline, no phone: the model's first token was 0.52 s at a 17k-token prompt, 0.54 s with fewer tools (13k) and 0.57 s with half the instructions (12k), 2 runs each (`make measure-delay`), so prompt size is not a lever. A held reply ends a median 0.29 s (max 0.54 s) after its first token, which is what the hold cost per model request.

## Causes found

- **Thrown-away drafts.** LiveKit keeps an early draft only if the caller's line reaches the model unchanged; the worker appends server notes to most lines. 0 of 67 drafts were kept on the live worker's log, 0 of 36 and 0 of 13 on box runs. Preemptive generation off (joco/stripped d8eb92f).
- **The filter's hold.** From the first ZIP check to the confirmed read-back the worker spoke nothing until the model finished. Narrowed so a lead-in streams and only a sentence that could restate the address or number waits (built and unit-tested, on branch joco/latency-leftovers).
- **The long wait.** The turn detector judged a quarter of caller turns unfinished and waited the full 1.5 s. 0.9 s measured better above.
- **Half replies.** A question dropped by the filter left a reply that asked nothing (fixed, 4b160eb); replies offering choices without a question (prompt line, 09eb038).
- **Acknowledgments cut the agent off.** LiveKit lets plain voice activity interrupt in the first 1.0 s of every agent line; set to 0 so the adaptive detector decides (d1902ed, test fix 94fbd01).
- **Dropped first word.** A rig artifact: Deepgram's batch transcription drops a word that starts at the edge of the audio. Padding 0.3 s of silence fixes it (handed to the realism lane for #157).
- **Slow turn after "that's all".** Deepgram's final transcript came 1.2 to 1.9 s after short lines on live call 2. A switch to ask Deepgram to finalize at our own end of speech was built (`stt.finalize_at_end_of_speech`) and never measured.
- **"White Street".** Cartesia's Max voice says "Hoyt" ambiguously; the rig's ears heard White or Hoit in some runs. Fix would be a pronunciation entry in the tenant's local names.

## Left undone

- Long wait 0.9 s and dynamic endpointing (the dynamic arm was stopped before it finished).
- Read-back pace (address and number one step slower): built on top of joco/stripped (b7c5f7c, kept on branch joco/latency-leftovers); its arm never ran. Offline, the grouped read-back at today's speed measured 2.8 to 3.7 digits a second, still over the realism check's 3.0.
- The hold release on top of joco/stripped (457a0c1, on joco/latency-leftovers): built and unit-tested, its arm never ran.
- Deepgram finalize: built behind a switch on this branch, never run.
- Rig tooling on separate branches (mid-line pause, noise floor, speech cache): joco/rig-pause-noise, not merged.
- Decisions 100 to 103 were reserved for this lane and not written; nothing here was decided beyond what joco/stripped carries.

Spend: about $12 of the $20 cap (rig runs about $0.35 for four calls, text-harness must-pass runs about $1.90 each).
