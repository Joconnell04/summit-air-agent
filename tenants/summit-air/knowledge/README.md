# Summit Air knowledge

Plain markdown the agent can use in answers (docs/call-journeys.md, company knowledge). The tool server loads every `.md` file here except this one and returns them with `/calls/start`. A different company swaps the folder. Everything here is ASSUMED until Summit Air confirms it. `hvac-basics.md` is the exception: it is listed under `lookup_knowledge` in agent.yaml, so it stays out of `/calls/start` and only `lookup_hvac_info` reads it.
