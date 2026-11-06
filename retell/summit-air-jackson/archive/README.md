# Archived prompts

Versions of the Summit Air Jackson prompt kept with a Retell backup, so we can switch back in one call. Every version is also in git history (see `../CHANGELOG.md`).

## prompt-cs28.md

Change set 28, the live prompt on agent_a3e38e6aa3d4e75dfac12aafc8 (llm_12eba5f1d4a3b067f0635097de30) when the demo moved to the socket build. 2,775 words (MEASURED with `wc -w`). The single-prompt agent is the fallback.

Retell backup: a copy of the live LLM and agent, with this prompt and the same tools, variables, model and voice settings. It was checked field by field against the live LLM when it was made.

- Agent "Summit Air Jackson backup cs28": agent_4da5e0c84a9df9dde0f0dc485d
- LLM: llm_f6ff12ee7bef08662f8d2ae2ecae

The backup isn't on any phone number. Its tools call the live tool server, as Jackson's agent's do.

To switch back in one call, point Jackson's agent at the backup LLM:

```bash
curl -X PATCH https://api.retellai.com/update-agent/agent_a3e38e6aa3d4e75dfac12aafc8 \
  -H "Authorization: Bearer $RETELL_API_KEY" -H "Content-Type: application/json" \
  -d '{"response_engine": {"type": "retell-llm", "llm_id": "llm_f6ff12ee7bef08662f8d2ae2ecae"}}'
```

Or put the backup agent on the number with `PATCH /update-phone-number` (`inbound_agent_id`). Then run `scripts/retell_snapshot.py` so the repo matches what's live.

An earlier backup of change set 24 is also in Retell: agent "Summit Air Jackson backup cs24", agent_4da3f38574248b193b4456f82e, LLM llm_c959783049e4e07a385f689985d6. Use cs28 unless there's a reason to go back further.
