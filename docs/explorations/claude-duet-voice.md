# What makes Claude's voice feel natural, and what Retell lets us copy

Claude Duet is not a voice product: it is a proactive background agent on Opus 5.5, announced 2026-10-21, that you can message by typing or voice, and a reviewer says it has "just transcription" (SOURCE [1][2]). Claude's actual voice mode is turn-based, not full duplex, and I found no Anthropic latency figure and no voice prompting guidance (SOURCE [3][4][5]). The natural-feel techniques that transfer to us therefore come from Retell's own docs and builder evidence, and most of Retell's dials are already set in `voice/retell/build.py`.

## 1. Question

What is "Claude Duet", what makes Claude's voice conversation feel natural, and which of that can the Retell agent copy?

## 2. Why it matters to Summit Air

The brief wants a call that sounds like a competent receptionist, and Jackson's live calls found dead air and wait time (issue #149). Turn-taking and reply length are the levers left once the tool server owns the rules (DECISIONS 112). Premise check: Engadget says Claude's turn-based design should feel less natural than OpenAI's full-duplex GPT-Live (SOURCE [3]), so Claude is not the benchmark to copy.

## 3. Options

What Claude voice does, and what we can do about it. "Set" means already in `SPEECH` in build.py (MEASURED, file read).

| Technique | Retell knob | Status | Source |
|---|---|---|---|
| Waits for the caller to finish, tolerates pauses; listens hands-free; interrupt by speaking | `responsiveness`, `enable_dynamic_responsiveness`, `interruption_sensitivity` | Set (0.9, on, 0.8) | [4][6] |
| Pace choice Slow/Normal/Fast | `voice_speed`, `enable_dynamic_voice_speed` | Speed 1.0; dynamic off | [7][6] |
| Backchannels ("mm-hmm") | `enable_backchannel`, `backchannel_frequency`, `backchannel_words` | Set (0.4). Retell's default-word table lists ElevenLabs, OpenAI, Deepgram voices, not Cartesia, so firing on our voice is unverified | [8] |
| Short, human turns; acknowledge, then answer | Agent Handbook: `handbook_config.conversational_personality` (about 910 tokens), `natural_filler_words` (about 100), `high_empathy` (about 70) | Not set | [9][10] |
| Emotion, sighs, pauses in the voice | `enable_expressive_mode`, `expressive_emotion_tags`; `voice_temperature` | Not set. Expressive mode is platform voices only and the docs don't mention Cartesia | [11][6] |
| Say something while a tool runs | Tool `speak_during_execution`, `execution_message_type` (`static_text` or `prompt`), `execution_message_description` | Off. Speaks on every call of the tool, not only slow ones (SOURCE voice/retell/README.md); whether it speaks once or repeatedly is unverified | [12] |
| Silence prompts | `reminder_trigger_ms`, `reminder_max_count` | Set (10 s, 2) | [6] |
| Background ambience | `ambient_sound`, `ambient_sound_volume` | Not set. Any realism gain is ASSUMED | [6] |
| Early or speculative replies | None on Retell; fast fallback is a backup model that bills both | Not set | [13] |
| Voice prompt style rules | Prompt text | Anthropic published none; its Haiku 5.5 system prompt has one product line on voice (SOURCE [5]). Retell says "a longer prompt is not a better prompt" | [14] |

Two cautions. The Handbook's conversational preset includes "internal colloquial rewrite behavior" (SOURCE [6]), and Retell's model already rewords about half of our fixed lines (SOURCE retell-exact-speech.md), so a rewrite could hit the gas and read-back lines. A filler line at tool start once made the real reply wait 7 to 10 s (MEASURED, Bobert, an earlier personal practice voice agent, not a person; see [from-other-projects/bobert](../../from-other-projects/bobert)).

## 4. Recommendation

Try these five, in order of likely effect on sounding human (all ASSUMED rankings, none tried; no agent was changed):

1. A short voice-style block in `prompts/`: two short sentences at most, one question per turn, a two-to-four-word varied acknowledgement before answering, numbers said as spoken. Retell's Professional tone uses "Acknowledge, Statement, Next Step" (SOURCE [9]); builders say shorter chunks beat faster models (SOURCE 019-forum-stacks.md).
2. A one-line static "one moment" on the two slow tools only (lookup, booking), then measure the turn against the Bobert result. Kills the silence after a tool.
3. Listen to recorded calls for backchannels. If none fire on the Cartesia voice, turn the setting off rather than guess.
4. On a copy agent, turn on `natural_filler_words` and `conversational_personality`; keep only if safety lines still match word for word.
5. Test `enable_dynamic_voice_speed` and a lower `voice_temperature`.

What would change this: a voice-mode demo from Jackson that shows a technique not listed here, or Anthropic publishing a voice guide.

## 5. Open questions for Jackson

- Where did "Claude Duet" come from? If it was a voice demo, which one: the Claude app's voice mode, or something else?
- Is a "one moment" line on every lookup and booking acceptable, given Retell can't skip it when the tool is fast?
- May I build a copy agent for item 4 without touching the live one?

## 6. Sources

Read 2026-11-05.

1. https://claude.com/resources/articles/claude-duet (Anthropic, 2026-10-21)
2. https://thenextweb.com/news/anthropic-claude-duet-ai-agent (2026-10-22)
3. https://engadget.com/2221938/claude-voice-mode-just-got-smarter (2026-07-23)
4. https://support.claude.com/en/articles/11101966-using-voice-mode
5. https://platform.claude.com/docs/en/release-notes/system-prompts/claude-haiku-5-5 (2026-10-26)
6. https://docs.retellai.com/api-references/create-agent.md
7. https://www.engadget.com/2231293/how-to-use- -voice-mode/ (2026-08-09)
8. https://docs.retellai.com/build/interaction-configuration.md
9. https://docs.retellai.com/build/agent-handbook.md
10. https://docs.retellai.com/build/conversational-mode.md
11. https://docs.retellai.com/build/expressive-mode.md
12. https://docs.retellai.com/api-references/create-retell-llm.md
13. https://docs.retellai.com/build/llm-options.md
14. https://docs.retellai.com/build/prompt-engineering-guide.md
