# Caller styles

A caller style is how a simulated caller talks, kept apart from who they are on a given call. A
scenario sets `caller_style: <name>` to use `evals/personas/<name>.yaml`; the scenario's `persona`,
`hidden_facts` and `script` still say who is calling and what they want.

- `manner`: added to the persona in the caller prompt.
- `lines`: real lines to model the register on, added to the script as examples, not a script.
- `timing`: read by the audio rig only. `patience_s` is how long the caller waits on a silence
  after a line that asks nothing before speaking up; `cut_in_after_s` is how long an agent turn can
  run before the caller cuts in.

`jackson.yaml` is drawn from Jackson's live calls 1 and 2 (`evals/live_calls/`).
