# Build brief: Retell baseline

Branch: `build/voice-retell`. Depends on the tool server (brief 02). Not a token effort: this is half of the voice-platform bake-off (DECISIONS.md 004, docs/bakeoffs.md), and if Retell wins, it becomes the product. It has to be set up as well as Retell allows, or the comparison means nothing.

## Goal

A Retell agent on the existing 678 number that runs the same conversation as our LiveKit stack, against the same tool server, with the same prompts and as close to the same models as Retell offers, measured the same way. The bake-off result should come down to the platform, not to setup differences.

Read first: CLAUDE.md (Retell rule: versioned endpoints only), docs/tools.md, docs/bakeoffs.md (bake-off 1: hypothesis, scorecard, decision rule), docs/call-journeys.md, docs/latency.md, docs/explorations/004-call-control-and-numbers.md (what Retell can and can't gate), docs/decisions/04-voice-platform.md, from-other-projects/bobert/ (the earlier practice project ran on Retell: its architecture, push script and lessons).

## Scope

- **Config as code** in `voice/retell/`: the agent, its LLM settings and custom functions, built from the same `prompts/` files the LiveKit worker uses and pushed with a script that only uses Retell's versioned API. Running the push twice changes nothing.
- **Same brain where possible.** Use the Claude model Retell offers closest to our `fast` profile, with the same prompt; record any difference in model or settings in the README, because it affects the comparison. If Retell's own LLM path can't match, test its custom-LLM mode (our server runs the model; Retell does ears, mouth and turn-taking) as a variant and say which was used.
- **Same tools.** Every tool in docs/tools.md as a Retell custom function pointing at the tool server on the summit-air server over HTTPS. Requests signed by Retell and verified by the server. `call_id` mapped to Retell's call id.
- **Same call lifecycle.** Retell's call-started and call-ended webhooks call `/calls/start` and `/calls/{id}/end`; caller and agent turns reach `/calls/{id}/turn` (through webhooks or the custom-LLM socket), so the safety check, logging and owner view see Retell calls exactly like LiveKit calls.
- **Safety gating.** Built-in end-call and transfer off; our server's `end_call` used instead. Where Retell can't let the server veto a hang-up or run the per-turn safety check before the agent replies (exploration 004), document the gap and test it in the safety scenarios. A Retell loss on safety is a valid bake-off result.
- **Tuned turn-taking.** Interruption sensitivity, responsiveness, backchannel and end-of-call settings tuned for phone audio and digit answers, starting from Retell's guidance; the chosen values and why go in the README. Voice: the Cartesia voice our stack uses if Retell offers it, otherwise the closest match, noted.
- **Measured the same way.** Per-turn timings from Retell's call data mapped into our trace format, so `make trace` and the bake-off report show both stacks side by side. Cost per call from Retell's billing data.
- **Eval adapter.** The harness drives this agent through Retell's text chat interface with the same scenarios (brief 04); no Retell simulator is used for scoring.
- **Phone.** Bind the 678 number to this agent, replacing the old practice agent's binding.

## Must be right, each with a test

Push script is idempotent and uses only versioned endpoints (test against recorded responses); webhook signature verification; Retell call ids map to our call ids; trace mapping produces the same fields as LiveKit's.

## Done when

`make check` passes; a real call to the 678 number reaches the tool server, books in the mock and shows in the owner view and `make trace`; the smoke suite runs against it through the harness; the README lists every setting and every difference from the LiveKit stack; the reviewer agent has checked the branch.
