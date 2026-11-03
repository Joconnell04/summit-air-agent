# Prompts

What the model is told on a call, split by job so each part can be changed and tested on its own. Both voice layers load these files through `assemble.py`, so the LiveKit worker and the Retell baseline run the same conversation. Every change runs the eval suite before merge.

| File | Job |
|---|---|
| `identity.md` | Who the agent is and how it sounds; customer notes shape manner, never rules |
| `spine.md` | The steps every call shares, in order (docs/call-journeys.md, the spine) |
| `journeys.md` | What each kind of caller wants and how the call goes (journeys 1 to 14) |
| `tool-use.md` | How to read tool results and refusals, and the `[server]` notes |
| `safety.md` | What the model does around the safety lines, which the voice layer speaks for it |
| `house-guidance.md` | The fixed preamble over the owner's own guidance by stage (app/rules.py); added after the journeys only when there is some |
| `guidance-check.md` | Not part of a call: the instructions for the check on an owner's saved draft (app/guidance_check.py), which refuses only a note or line that contradicts a locked rule |
| `tools.yaml` | One description per tool; the arguments come from the tool server's request models |

`assemble.instructions(start)` joins the files in that order (with the owner's guidance after the journeys when `/calls/start` returned any), puts in the company name, and adds the call context: the tenant's knowledge folder, the local time, and what `/calls/start` found before hello. `assemble.tool_specs(job_types)` gives one entry per server tool with its JSON-schema arguments (references inlined, `call_id` left out, job types limited to the tenant's own).

No company fact is written into these files (a test checks for the company name and phone numbers); everything specific comes from the tenant folder through the call context (docs/template.md).

The rules that must happen (safety lines, read-back before booking, commercial refusal, hang-up on a safety call) are enforced by the server whatever the prompt says. The prompt makes the agent do them smoothly; it is not the gate.
