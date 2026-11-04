# Gemini Live

An entrant in the simulated audio bake-off (DECISIONS.md 026, docs/bakeoffs.md bake-off 6): Google's speech-to-speech model with our tool server behind it. Only the rig adapter exists (`rig_adapter.py`); nothing outside `voice/` imports this folder.

- **The turn check runs late.** Gemini Live answers as soon as it hears the caller stop and can't be told to wait, so the caller's transcribed words reach `/calls/{id}/turn` when the reply starts. If the server asks for the safety lines, the model is interrupted and told to say them exactly; the caller hears the start of its own reply first. This is a real difference from our stack and from the OpenAI entrant, where the server reads every line before any reply.
- **Safety text is the model's to say**, checked word for word by the tool server.
- **Tools** are forwarded to the tool server with an Idempotency-Key on writes; `end_call` drops the line only when the server returns `hang_up: true`.
- Model, voice and prices are `realtime.gemini` in config/models.yaml. Cost is counted from the usage Gemini reports with each turn.

Run it with `make rig STACK=gemini_live` (evals/README.md, "Audio rig"); it needs `GEMINI_API_KEY`.
