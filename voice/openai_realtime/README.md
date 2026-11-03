# OpenAI Realtime

An entrant in the simulated audio bake-off (DECISIONS.md 026, docs/bakeoffs.md bake-off 6): one speech-to-speech model hears, decides and speaks, with our tool server behind it. Only the rig adapter exists (`rig_adapter.py`); nothing outside `voice/` imports this folder.

- **The turn check runs first.** OpenAI's turn detection runs with `create_response` off. When the caller's words come back transcribed, they go to `/calls/{id}/turn`, and only then is a reply asked for, with any `[server]` notes. This keeps the server ahead of every reply, as on our stack, and the transcription wait is part of the measured delay.
- **Safety text is the model's to say.** The model is the voice, so the server's text is sent as an instruction to say it exactly; the tool server checks the words the model reports. Whether it complies is what this entrant tests (DECISIONS.md 006).
- **Tools** are forwarded to the tool server with an Idempotency-Key on writes; `end_call` drops the line only when the server returns `hang_up: true`, after the goodbye has played.
- Model, voice, transcription model, turn-detection silence and prices are `realtime.openai` in config/models.yaml. Cost is counted from each response's token usage.

Run it with `make rig STACK=openai_realtime` (evals/README.md, "Audio rig"); it needs `OPENAI_API_KEY`.
