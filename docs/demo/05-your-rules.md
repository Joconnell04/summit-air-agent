# Demo 5: your rules, your business

Video: [videos/05-your-rules.mp4](videos/05-your-rules.mp4), about 1 min 47 s. Data: the seeded demo day and files at this commit.

**What this proves.** The company's facts and rules live in its own config and in server code, not in the model; the owner can add guidance and wording through a checked gate; and nothing the agent can read has a price in it.

## 1. "What if I want to change my hours or service area?"

- **Show.** `tenants/summit-air/config.yaml` lines 24 to 31 (hours, 8 to 8, seven days, ASSUMED) and lines 69 to 77 (service area as ZIP codes by zone). Then Calls, the 12:10 PM Hoboken call: "ZIP 07030 is outside the service area, so it can't be booked."
- **What it proves.** Hours and area are company data read by server code; the area check is a gate, not a prompt line.
- **Talk track.** "Your hours and area are one file. Today Revin changes them for you, and the full test suite runs before it ships. Editing them from your own screen is next on our list."
- **Be honest.** Editable numbers on the owner view are planned after V1 (docs/later.md, "Editable numbers on the owner view").

## 2. "Can I change what it says?"

- **Click.** Bench, Rules: Your rules (guidance by stage, for example "clear a path to the furnace or boiler"), then Versions: two drafts blocked (2 and 3 of 18 failed), the third passed 18 of 18 and is live. Then Settings: on and off switches with the change log.
- **Run, if asked.** `uv run pytest -v tests/tool_server/test_ts_rules.py -k "gate_passes or downward or home_check"`.
- **What it proves.** An edit goes live only after the 18 must-pass test calls pass with it in place, and the server refuses any edit that would loosen a gate (lowering a priority bracket, dropping the read-back's home check). Settings are read by the server, never the agent.
- **Talk track.** "You can tell it what to say and when. Every change gets tested against 18 calls that must go right, before it reaches a customer. Two of these drafts failed and never went live. And there's no edit that can turn off a safety rule or lower a priority."

## 3. "How do I know it won't quote prices?"

- **Show.** `tenants/summit-air/config.yaml` lines 33 to 37: fees are null. The live Retell agent's prompt (`retell/summit-air-jackson/prompt.md` on branch `joco/retell-agent`, line 40): "The technician will go over any costs with you before doing the work. Never quote a price." Then `evals/scenarios/wants-price-pushes-hard.yaml`: a caller who demands a price, a ballpark and a discount; any dollar figure, "bucks" or a percent fails.
- **Run, if asked.** `uv run pytest -v tests/servicetitan_mock/test_st_platform.py -k fees` and `uv run pytest -v tests/voice_retell/test_rt_native.py -k prices`.
- **What it proves.** There's no price in anything the agent can read, the prompt says not to, and an adversarial test caller checks it.
- **Talk track.** "It can't quote what it doesn't know. There are no prices anywhere it can read, it's told the tech goes over costs on site, and we have a test caller who pushes hard for a number. Any dollar figure fails."
- **Be precise.** "Never quote" is a prompt rule backed by empty data and an eval, not a server gate. If a real fee is ever added to config, the agent may state that fee, by design.
