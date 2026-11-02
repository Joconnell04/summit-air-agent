# Build brief: Retell baseline

Branch: `build/voice-retell`. Small lane. Depends on the tool server.

## Goal

The baseline our LiveKit stack has to beat (DECISIONS.md 004): a Retell agent on the existing 678 number calling the same tool server with the same prompts, so the bake-off compares like with like.

Read first: CLAUDE.md (Retell rule: versioned endpoints only), docs/tools.md, docs/bakeoffs.md, docs/explorations/004-call-control-and-numbers.md, from-other-projects/bobert/architecture.md.

## Scope

- `voice/retell/`: agent config as code (LLM, prompt built from `prompts/`, custom functions pointing at the tool server) and a push script using only Retell's versioned API.
- Built-in end-call and transfer turned off; our server's end-call tool used instead, as far as Retell allows (exploration 004); note any gap honestly in the README.
- Tool server reachable from Retell over HTTPS on the summit-air server; requests signed and verified.
- Bind the 678 number to this agent, replacing the old practice agent.
- An adapter so the eval harness can drive this agent through Retell's text chat interface.

## Done when

`make check` passes; a real call to the 678 number reaches the tool server and books in the mock; the smoke suite runs against it through the harness; the reviewer agent has checked the branch.
