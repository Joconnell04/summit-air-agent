# Retell knobs

The dials on Jackson's Retell agent, "Summit Air Jackson" (`agent_a3e38e6aa3d4e75dfac12aafc8`, LLM `llm_12eba5f1d4a3b067f0635097de30`), read from Retell's API on 2026-11-05: what each does, where it is set now, and what we measured where we did. He edits the agent himself with Retell's builder; this page changes nothing on it.

Field names are Retell's: agent fields from Create Agent (SOURCE: https://docs.retellai.com/api-references/create-agent), LLM fields from Create Retell LLM (SOURCE: https://docs.retellai.com/api-references/create-retell-llm). Most measurements come from replaying Jackson's own replies from live call 3 (call_ca9bfcec0b968db42ac2f428bea) into Retell web calls with `voice/retell/turn_probe.py`, two passes per setting; results are in `evals/results/retell-turn-probe/`. The others come from scripted phone calls, named by call id. Delays are Retell's own e2e latency, or the probe's time from the end of the caller's clip to the agent's first sound.

## Two things to fix first

- **Two hang-up functions.** The LLM has our custom `end_call` (to the bridge) and Retell's built-in End Call, named `hang_up`. The built-in one drops the line 0.02 to 0.07 s after the last word (MEASURED, native calls call_bd7dcf9c2b49b5a2c13eb37ec57 and call_411b6aa0d8810886254f4d3e558) and skips the server's end gate. Keep one. Ours waits 1.5 s after the goodbye and lets the server refuse (see Functions).
- **No call context.** The prompt uses none of the dynamic variables the bridge sends, so the agent never sees what the server found before hello (the caller's account, open visits, a dropped safety call) and always opens with its fixed `begin_message`. See Dynamic variables.

## Agent Handbook (`handbook_config`)

Behaviour presets Retell adds to the prompt. Voice only: Retell's chat agents ignore them, so text chats can't test them.

| Toggle | Now | What it does | Measured |
|---|---|---|---|
| `default_personality` | on | Professional call-centre rep baseline. | Not measured. |
| `conversational_personality` | off | Colloquial rewrite of each reply. | On, it split replies into a quick first phrase plus the rest, with a pause between ("Great.  You're booked..."): 3 of 6 replies split on, 0 of 6 off, and no slower (median 2.82 s on, 2.63 s off; MEASURED, web calls on the native copy). Keep it off. |
| `natural_filler_words` | unset | "Um", "you know". | Not the cause of the splits (3 of 6 split with fillers off). |
| `high_empathy` | unset | Warm acknowledgement of the caller's problem. | Not measured. |
| `echo_verification` | unset | Repeats important details back. | On native it added a confirmation turn before our read-back. The server's `read_back` already confirms the address and number. |
| `speech_normalization` | unset | Numbers and dates said as words. | Not measured. |
| `smart_matching` | unset | Treats near-matches ("St." / "Street") as the same. | Not measured. |
| `ai_disclosure` | unset | Says it is a virtual assistant when asked. | Not measured; our prompt says so too. |
| `scope_boundaries` | unset | Stays within the prompt; doesn't invent details. | Not measured. |
| `nato_phonetic_alphabet` | unset | Spells using "B as in Bravo". | Left off on native: it would sound robotic on every street name. |

## Listening and turn-taking

| Field | Now | What it does | Measured |
|---|---|---|---|
| `stt_mode` | `fast` | Transcription mode: `fast` or `accurate`. | **`fast` missed Jackson's quiet one-word "sure" in 2 of 2 passes**, with noise cancellation on. `accurate` caught all 12 short replies, and was the only mode that waited through his mid-sentence pause ("I'm just wondering ... if I can cancel"); `fast` answered into it 5.9 s in, on every setting (MEASURED). |
| `denoising_mode` | `noise-cancellation` | `no-denoise`, `noise-cancellation`, or `noise-and-background-speech-cancellation`. | With `fast`, `no-denoise` and background-speech cancellation both caught "sure"; plain noise cancellation didn't. With `accurate`, noise cancellation caught everything. |
| `responsiveness` | 1.0 | How soon the agent answers (0 to 1). | With `accurate`: median answer 1.75 s at 1.0 without dynamic, 2.05 s at 0.9 with dynamic, and both still waited through the pause (MEASURED). |
| `enable_dynamic_responsiveness` | off | Adapts the wait to the caller's pace. | See above. |
| `interruption_sensitivity` | 0.8 | How easily the caller cuts the agent off. | Retell's guidance for background noise is 0.8 (SOURCE: https://docs.retellai.com/build/handle-background-noise.md). Not measured. |
| `enable_backchannel`, `backchannel_frequency`, `backchannel_words` | on, 0.4, "mm-hmm", "okay", "uh-huh", "gotcha" | Short acknowledgements while the caller talks. | Never sounded over a replayed caller (MEASURED). The "Sure," that cut Jackson off on call 3 was a reply, not a backchannel. |
| `begin_message_delay_ms` | 800 | Wait before the greeting (0 to 5000). | Native uses 0. |
| `end_call_after_silence_ms` | 45000 | Retell hangs up after this much silence, without asking the server. | Not measured. |
| `max_call_duration_ms` | 1200000 | 20-minute cap. | Not measured. |

The combination tested best for short replies: `stt_mode` `accurate`, noise cancellation, `responsiveness` 1.0, dynamic off, backchannel on. It heard 12 of 12 replies, with no cut-in, at a median 1.70 s (MEASURED, `live` variant). Jackson's agent still runs `fast`.

## Reminders

| Field | Now | What it does | Measured |
|---|---|---|---|
| `reminder_trigger_ms` | 20000 | Silence before the agent checks in. | At 6 s, native nudged Jackson three times while he thought (call 2); native moved to 10 s. |
| `reminder_max_count` | 1 | Check-ins before it goes quiet. | 1 on native too. |

## Voice and Expressive mode

| Field | Now | What it does | Measured |
|---|---|---|---|
| `voice_id` | `retell-Cimo` | The voice. Retell's platform voices (`cartesia-Max`, `retell-Cimo`, ...) or a custom import (our Max is `custom_voice_c37419aaeaa68016fe1138f8a2`). | Jackson picked Cartesia Max with the "happy" emotion from four samples (`data/voice-samples/`, untracked). Retell can't pass Cartesia's emotion setting to a custom voice. |
| `voice_model` | unset | The TTS model (`sonic-3`, `sonic-3.6`, ...). | Recorded through Retell, questions fell at the end on `sonic-3.6` and rose on `sonic-3` (MEASURED, rough pitch track, `data/voice-samples/retell/`). |
| `voice_temperature` | 0.5 | Stability (0 to 2); lower is steadier. | Not measured. |
| `voice_speed`, `volume` | 1.0, 1.0 | Pace and loudness. | Not measured. |
| `enable_expressive_mode` | off | Lets the agent add emotion tags to its speech. **Platform voices only** (SOURCE: https://docs.retellai.com/build/expressive-mode.md). | On `cartesia-Max` with `happy` and `empathetic`, questions rose about 75% in pitch on the last word, against about 50% for the custom Max (MEASURED, rough). The closest Retell gets to "Max happy". |
| `expressive_emotion_tags`, `expressive_mode_prompt` | five tags, a custom guide | Which tags it may use, and when. | Unused while expressive mode is off. |

## Knowledge base (`knowledge_base_ids`, `kb_config`)

Now: no knowledge base attached; `kb_config` filter 0.6, top 3, with a retrieval instruction. Retell puts what it retrieves inside the caller's turn, and the model answers that text as if the caller said it:
- Jackson's agent said "That context block isn't something I'll act on" and "I'm not going to use that reference material" three times on one call (call_57cc795f65a03c33612bae0064f).
- Native at a 0.75 threshold: "That knowledge based text came through in your message", three times (call_f916fac3014142085f3e5e93fab).

With the same facts in the prompt instead, the next call had none (call_411b6aa0d8810886254f4d3e558). The facts are short (hours, areas, membership, equipment), so they fit in the prompt.

## Functions (`general_tools`)

| Kind | On his agent | How it works |
|---|---|---|
| Custom functions | All 15 of our tools, each a POST to the bridge's `/retell/tools/<name>`, 10 s timeout, `speak_during_execution` off | The bridge posts any new transcript lines, forwards the call with our call id and an Idempotency-Key, and returns the server's envelope (`ok`, `say`, `data`, `refused`). Every gate is the server's. `speak_during_execution` with a short line ("Let me check that.") covers a slow lookup; on native it made "Booking that now." come after the confirmation, so leave it off for `book_appointment`. |
| Our `end_call` | yes | If the server allows it (`data.hang_up`), the bridge calls `stop-call` 1.5 s after the agent's last spoken word, timed from Retell's word timestamps. It re-checks up to four times if the goodbye is still going. A caller line other than a goodbye in that wait keeps the call open. If the server refuses (a safety call before the sign-off), nothing hangs up (`voice/retell/bridge.py`). |
| Built-in End Call | yes, as `hang_up` | Hangs up as soon as the agent stops speaking; no server gate. Remove it, or remove our `end_call`. |
| Built-in Call Transfer | no | `transfer_call` with a predefined number and cold transfer; it bypasses the server's transfer decision. |
| Code tool | no | Runs JavaScript in Retell's sandbox, but takes **no arguments from the model**: it reads only dynamic variables (SOURCE: https://docs.retellai.com/build/single-multi-prompt/code-tool.md). Letter counting through it needed an extraction step first, and the model then described the question aloud on 6 of 6 first turns (MEASURED, text chats). The bridge's `/retell/letters` takes the arguments directly; it still narrated on 3 of 6, so it stays off. |

## Safety lines

The server decides when the gas or carbon-monoxide lines are said, and checks that they were said word for word.
- On each `transcript_updated` webhook, the bridge posts the caller's new line to the tool server's turn check. If it answers with a safety action, the bridge calls Update Live Call with `call_control.trigger_response: true`. Its `additional_context` reads "Say this to the caller now, before anything else, exactly as written...", then the lines, and the agent says them at once.
- If a tool call reaches the bridge first, the same text comes back in that tool's result as `speak_first`.
- Other server hints go as `additional_context` without `trigger_response`.

Until an agent turn contains the lines word for word, `end_call` is refused with `gate_safety`, and the refusal's `say` is the lines again. So the prompt should never have the agent say safety instructions of its own. His prompt has its own wording, which is a second and third way for the lines to be heard.

## Pronunciation and keywords

| Field | Now | What it does |
|---|---|---|
| `pronunciation_dictionary` | 17 entries from `tenants/summit-air/names.yaml`, e.g. Houston Street as /ˈhaʊstən/ (HOW-stun) | IPA for local names. Retell says only some voice providers honour it (SOURCE: Create Agent, `pronunciation_dictionary`); not checked on `retell-Cimo`. |
| `boosted_keywords` | 24: company and equipment words, plus the tenant's street names | Biases transcription toward them. Native also adds the five boroughs and "tune-up". |

## Guardrails, privacy and memory

| Field | Now | What it does |
|---|---|---|
| `guardrail_config` | input: jailbreaking; output: harassment, self-harm, sexual exploitation, violence, illicit activity, regulated professional advice, child safety | Retell's topic filters on what the caller says and what the agent says. Not measured; "regulated professional advice" is worth one test, since gas-safety instructions could be read as advice. |
| `pii_config` | post-call, no categories | Redaction in stored transcripts and recordings after the call. |
| `enable_dnc_detection` | on | Detects "don't call me". The server also has `mark_do_not_call`. |
| `contact_memory_config` | read on, update off | Remembers callers across calls. On test calls it carries one call into the next; native has it off. |
| `post_call_analysis_model` | `gpt-5.6-terra` | The model for Retell's post-call analysis. No `post_call_analysis_data` fields are set, so nothing is extracted. |

## Dynamic variables (the bridge's inbound webhook)

The numbers send every incoming call to the bridge's `/retell/inbound` before it connects (`inbound_webhook_url` on the phone number). The bridge starts the call on the tool server, which does the lookup before hello, and returns `call_inbound.dynamic_variables` (`voice/retell/conversation.py`):

| Variable | Holds |
|---|---|
| `summit_call_id` | Our call id; every function and webhook is posted under it. |
| `greeting` | The first line: by name for one matched customer, the recording notice when one applies, or the safety lines when a dropped safety call comes back. |
| `instructions` | The whole long prompt for this call (the baseline agent uses it as its prompt). |
| `call_context` | Only the prompt's "This call" part: the time, notes for this caller, and what the server found before hello. Used by the native agent's short prompt. |

To use them, the LLM's `begin_message` would be `{{greeting}}` and the prompt would include `{{call_context}}`. His agent uses neither. It greets every caller with "Thank you for calling Summit Air. How can I help you today?", and doesn't know a returning customer or their open visit until a tool tells it.
