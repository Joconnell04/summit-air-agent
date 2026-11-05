# Retell Conversation Flow, round 1: our design on Retell's stage graph

Issue #133. Does Retell's Conversation Flow, built to mirror our design (exploration 017), do what our stack does as well or better? Every number here is MEASURED from the results files named, through the text harness (evals/README.md), unless it says otherwise.

**In short.** No. The flow kept every gate: the safety lines were said word for word on every safety call (79 of 79 checks on the standard suite, 36 of 36 on must-pass), no booking went through without a confirmed read-back, no commercial or out-of-area caller was booked, and no call ended or transferred except on the server's answer. But it passed 53% of the standard suite against our stack's 92%, and 45% of the must-pass set against 88%. On the scenarios the Retell eval instance can set up the same way our harness does, it was 61% against 92%. Almost all of the gap is how the agent talks: it read the address back again and again, asked two questions at once and described its own tools, which is what our speech filter drops before anything is spoken (DECISIONS.md 060). Retell streams the model's text straight to the voice, so a flow has no place to run that filter. On the audio rig it was also the slowest: 2.63 s median on a plain turn against our stack's 1.90 s and the single-prompt agent's 1.57 s, because every caller line goes through our server's turn check before the reply. The flow is not put on (678) 336-6730.

## 1. What ran

The flow is `voice/retell_flow/` at commit `ecacd0f`: 20 nodes for voice, 14 for chat (exploration 017, "The flow"), the same per-call prompt as our worker, Claude Haiku 5.5 on Retell at temperature 0.3, and every gate on our tool server through the shared bridge. The chat agent is `agent_d364ca011da486858a19c1baca` on flow `conversation_flow_cff52f11aeae`; the voice agent is `agent_f391ae33ea3d7f525c1ee109b4` on `conversation_flow_7867b3fb00ef`.

Before the paid runs, the reviewer agent read the build against exploration 017 and the spec twice; what it found and what was fixed is in exploration 017, "What the review changed".

| Run | What | Results file |
|---|---|---|
| Smoke, twice | 8 scenarios each, while building | scratch only, not kept |
| Must-pass, first | 21 scenarios x 2; spoiled by repeat-call linking (section 4) | `evals/results/20261104-212901-retell_flow-fast-custom.json` |
| Must-pass | 21 x 2 | `evals/results/20261104-215403-retell_flow-fast-custom.json` |
| Standard | 62 x 2; 91 of 124 errored when the laptop lost DNS to the eval server | `evals/results/20261104-222214-retell_flow-fast-standard.json` |
| Standard, the errored scenarios | 46 x 2 | `evals/results/20261105-000710-retell_flow-fast-custom.json` |
| Who ended the call, first fix | 3 x 2; lost the agent's last line on one call (section 4) | `evals/results/20261105-000941-retell_flow-fast-custom.json` |
| Who ended the call, and the one call that lost its last line | 4 x 2 | `evals/results/20261105-001542-retell_flow-fast-custom.json` |

The standard and must-pass sets below are each combined into one set of runs by `retell-flow-01-data/combine.py`: every scenario and sample comes from the first run unless that one errored or was scored with a harness fault fixed later, in which case it comes from the later run. The combined files are `retell-flow-01-data/standard-combined.json` (124 runs, 1 error) and `retell-flow-01-data/must-pass-combined.json` (42 runs).

Our stack's numbers are the reference when this lane started, `evals/results/20261104-185508-livekit-fast-standard.json`, and for must-pass the two-sample must-pass run before it, `evals/results/20261104-172343-livekit-fast-custom.json`, so both stacks ran the same 62 standard scenarios on the same tool server code. Main has since adopted a new reference with more scenarios and the call-handling, booking-flow and caller-changes lanes: 168 of 185 scored, 91% (`evals/results/20261104-234633-livekit-fast-standard.json`). The flow ran before those lanes' server changes reached the eval instance, so it is compared with the earlier reference; the gap is far wider than the one point between the two references.

## 2. Results

| | Our stack | Retell flow |
|---|---|---|
| Standard suite, passed of scored | 111 of 121 (92%) | 65 of 123 (53%) |
| Standard, the 51 scenarios set up the same way (section 4) | 93 of 101 (92%) | 62 of 102 (61%) |
| Must-pass set, passed of scored | 37 of 42 (88%) | 19 of 42 (45%) |
| Must-pass runs failing a code check | 3 | 7 |
| Standard runs failing only the grader | 10 | 37 |
| Standard runs failing a code check | 0 | 21 |
| Safety lines word for word (`safety_lines` checks passed) | 77 of 77 | 79 of 79 |
| Read-back before booking (`read_back` checks passed) | 81 of 81 | 83 of 83 |
| Bookings where one was expected (`booking` checks passed) | 107 of 107 | 106 of 109 |

**The checks the brief named.**
- **Safety lines word for word, every safety call:** yes, every one. On the single-prompt Retell agent the model paraphrased the gas lines on 5 of 6 audio calls (DECISIONS.md 038); in the flow they are a static-sentence node saying the server's text, so they can't be paraphrased.
- **Booking only after a confirmed read-back:** yes, 83 of 83. The server gate refused every early booking, and the read-back sentence itself was the server's, said from a static node.
- **No booking for commercial callers:** yes. `commercial-bakery` and `office-property-manager-declined` booked nothing on any run, and `out-of-area-known-caller` was declined every time.
- **Hang-up decided by the server:** yes, by construction: the end nodes are reached only through an edge on the `route` the bridge set from `end_call`'s answer (tests/voice_retell/test_rt_flow.py). No call ended any other way.

**Must-pass code-check failures on the flow, 7 runs in 4 scenarios:**

| Scenario | Runs | What | Whose |
|---|---|---|---|
| `already-outside-asks-for-visit` | 2 | No booking: the model mis-said the overnight window as "8 and 8" and dropped the hyphen in a Queens address ("23-15" sent as "2315"), and the caller gave up | The model, unfiltered |
| `asks-agent-to-call-911` | 2 | Never said "top priority" (our stack misses this too, both samples), and said "flag" and "read back" aloud | Our filter drops both words |
| `caller-declines-safety-advice` | 1 | Said "Need to confirm the read-back before booking." | Our filter drops it |
| `tune-up-known-caller` | 2 | `booking_lead_in`: the lead-in and the confirmation are two Retell messages, so the server logs two agent lines | How a flow records a reply (exploration 017, "Can't be done", 8) |

## 3. Where the gap is

Of the flow's 58 failed standard runs, 37 failed only the grader. Its notes name the same few things again and again (counted by keyword across the notes, so a note can count twice): the address read back again or with a "let me confirm" lead-in (31 notes), two questions in one turn (22), internal words such as "the lookup", "the system" or "flag" (14), and a callback time nothing had set (4). These are the rules in `prompts/assemble.py` `SpokenReply`, which our worker applies to the model's text before it is spoken and the eval harness applies the same way. On Retell the same prompt runs with none of them, and our model writes these things often enough that the filter is most of what separates the two.

The 21 standard runs failing a code check:
- **14 the eval instance or a flow can't set up** (section 4): the per-line voice speed the pace scenarios check (9 runs: `asks-agent-to-slow-down`, `asks-agent-to-speed-up`, `older-caller-gets-slower-pace`, `slower-address-read-back-only`, `sorry-what-twice-slows-down`), the after-hours clock (`after-hours-no-heat-cold`, 2), the booking system down (`booking-system-down`, 2) and an earlier dropped call (`dropped-call-resumes`, 1).
- **5 the model said or did unfiltered:** "call 911" and "carbon monoxide" off a safety call (`gas-worry-no-smell`, `no-heat-gas-stove`), "our system" and an out-of-area decline on a property manager's in-area building (`property-manager-books-building`), "dog" on `two-family-owner-residential`, and no booking on `co-op-board-asks-to-book`.
- **2 `booking_lead_in`** (above).

**What the flow does better than the single-prompt Retell agent.** The safety lines, the read-back, the booking confirmations and the safety sign-off are now exact, every time; the safety check and the `[server]` notes run before each reply instead of racing it; and the hang-up is Retell's own end node on the server's answer, with no timer guessing how long the goodbye takes. On those points it matches our stack.

**What it does worse than our stack.** Everything the model says that isn't a server line goes to the caller as written.

**Reply delay.** Not compared. In text chat every flow turn includes the turn-check function call, so the harness counts every turn as a lookup and times the whole chat request (median 2.14 s on must-pass), which isn't the delay a caller hears; our stack's figures come from the model in process. A phone call or the audio rig would measure it, and the flow didn't reach that step.

## 4. Harness faults found, and fixed

Three faults in the Retell eval path, shared with the single-prompt baseline, came up in this round. All are fixed on this branch, with tests.

1. **Repeat-call linking.** The Retell eval instance shares one tool server, and the harness runs a scenario's two samples back to back from the same caller ID, so the second was linked as a repeat call (S-38) and found the safety lines already said: on the first must-pass run nearly every second sample failed `safety_lines` with "no safety signal fired". The mock was reset per scenario; the tool server's calls were not. Now a debug endpoint allowed only on the eval instance (`POST /debug/forget-calls`, `TOOL_ALLOW_FORGET_CALLS=1`) ages every recorded call by two days, and both Retell adapters call it with the mock reset. Single-prompt baseline runs before this fix that ran more than one sample of a scenario carry the same fault.
2. **Who ended the call.** The bridge closed every chat as the agent's end, so a simulated caller who hung up after the safety lines failed `ended_by`. The first fix closed the call on the server before ending the chat, which lost the agent's last line when the caller hung up right after it (one call, the CO lines said and the server still at signal). Now the adapter writes the harness's reason into the chat's metadata and ends the chat, and the bridge posts the last lines, then closes the call with that reason.
3. **The eval instance runs in real time.** It can't take a scenario's clock, a booking system that's down, an earlier call, a scenario's weather or a failed transfer, as our harness's fresh world per scenario can. The 11 scenarios that need one are left out of the "set up the same way" row: `after-hours-tune-up`, `after-hours-no-heat-cold`, `mild-after-hours-no-heat` (clock), `booking-system-down` (booking system), `dropped-call-resumes` (earlier call), `inside-temp-only-when-asked` (weather), and the five pace scenarios, whose per-line voice speed a flow can't set.

## 5. On the audio rig: what the caller hears

Run the same way as the single-prompt agent in evals/rounds/bakeoff-audio-01.md: the smoke suite on the simulated audio rig, the same scenarios, caller voice and timing, each must-pass scenario three times, as Retell web calls on a voice agent kept for the rig (`voice/retell_flow/rig_adapter.py`) against the eval instance. The flow's results file is `evals/results/20261105-012409-retell-flow-rig-fast-smoke.json`; the other two rows are round 1's files, put through the same report (`make rig-report`).

| Stack | Calls | Plain median | Plain slowest 10% | Lookup median | Lookup slowest 10% | Must-pass checks held | Safety lines, server's check | Stack cost per call |
|---|---|---|---|---|---|---|---|---|
| Our stack | 12 | 1.90 s (29 turns) | 2.67 s | 1.96 s (29) | 3.96 s | 10 of 10 | 6 of 6 | $0.044 |
| Retell single-prompt | 12 | 1.57 s (36) | 2.60 s | 2.27 s (29) | 3.90 s | 5 of 10 | 1 of 6 | $0.180 |
| Retell flow | 20 | 2.63 s (41) | 3.60 s | 3.09 s (41) | 4.72 s | 13 of 18 | 4 of 6 | $0.150 |

Round 1's table showed our stack at 1.89 s plain; the report, rerun on the same file with its later talk-over rule (bakeoff-audio-02.md, section 4), now gives 1.90 s.

**The flow is the slowest of the three**, about 0.7 s behind our stack and 1.1 s behind the single-prompt agent on a plain turn. Every caller turn first goes through the turn-check function node, a round trip from Retell to the bridge on the summit-air server and back, before the model starts. That round trip is what puts the safety check and the `[server]` notes before the reply, so the flow pays for its control in time.

**The safety lines.** The flow said them from the server's text every time the server asked for them. Two of the six safety calls still failed the server's check, neither through the flow:
- `co-alarm-clear` #2: Retell's ears heard "my carbon alarm is going off" for "my carbon monoxide alarm", so the server's turn check never saw a safety signal and the model gave its own advice. On our stack (Deepgram Nova-3 hearing) the server's check passed on all three `co-alarm-clear` runs.
- `gas-smell-in-passing` #2: the lines were said word for word, but Retell's backchannel "Uh-huh", said while the caller was still talking, was written at the start of the agent's line, so the server's check that the first reply opens with the lines failed. Backchannel is a setting the flow shares with the single-prompt agent (voice/retell/README.md); turning it off on the flow is the obvious fix, not tried here.

The flow made sound while the caller was still speaking on 11 turns, against none for the single-prompt agent and none for our stack. The likeliest cause, not confirmed, is the same backchannel, along with the safety node's interruption setting of 0.

**Round 1's Retell safety result.** Its four "no safety signal fired" calls ran before the eval instance forgot earlier calls (section 4), so they were probably repeat-call links, as that round suspected; this round's rig runs forget them first.

## 6. Cost

$13.31 for the lane (MEASURED): $6.90 for the simulated caller and grader across the eight text runs (the results files' `cost_usd`), $2.49 of Retell chat charges for the 241 eval chats Retell's records cover, $0.0103 a chat (each chat's `chat_cost` from `GET /get-chat`), about $0.10 for the five prototype chats, and $3.82 for the 20 rig calls (the rig's own total, Retell's per-call charge included). Retell billed no post-chat analysis on the flow's chat agent after its model was set to `gpt-5.4-nano` (config/models.yaml `retell`).

## 7. Verdict

The flow does not pass every must-pass code check and is 39 points below our stack on the standard suite, so it does not go on the Retell number for live calls (issue #133's bar: every must-pass code check, and within 5 points). Retell stays out of the real-call round (DECISIONS.md 038), now for a second reason recorded in DECISIONS.md 087. On the audio rig it is also the slowest entrant, 0.7 s behind our stack on a plain turn, the cost of checking every caller line with our server before the reply. A static line before a transfer was not tested by voice, since no rig scenario transfers.
