.PHONY: setup test lint check ratchet ratchet-update eval eval-pairs eval-decisions replay langsmith eval-upload seed-st mock-st postman-st server postman trace \
	owner-demo rules-gate summary-check voice-livekit phone-setup deploy test-call measure-delay \
	retell-push retell-bridge retell-deploy retell-report rig rig-report

setup:
	uv sync
	git config core.hooksPath .githooks

test:
	uv run pytest -q

lint:
	uv run ruff check .

check: lint test ratchet

ratchet:
	uv run python scripts/ratchet.py

ratchet-update:
	uv run python scripts/ratchet.py --update

# Paid: runs scenarios against a stack (evals/README.md). The smoke suite is about $0.15 (MEASURED).
# make eval SUITE=smoke | TAGS=flow:safety,difficulty:rigorous | STACKS=livekit,retell | PROFILES=fast
ENV_FILE ?= .env
EVAL_ENV = $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE))

eval:
	uv run $(EVAL_ENV) python -m evals.run $(if $(SUITE),--suite $(SUITE)) $(if $(TAGS),--tags $(TAGS)) \
	  $(if $(STACKS),--stacks $(STACKS)) $(if $(PROFILES),--profiles $(PROFILES)) \
	  $(if $(SAMPLES),--samples $(SAMPLES))

# Paid: two calls at once against one world, racing for the same opening (evals/README.md, "Two
# calls at once"). make eval-pairs [PAIRS=all|two-callers-last-slot,...] [SAMPLES=3]
eval-pairs:
	uv run $(EVAL_ENV) python -m evals.run --pairs $(or $(PAIRS),all) $(if $(SAMPLES),--samples $(SAMPLES))

# Paid: the simulated audio bake-off, one stack per run (evals/README.md, "Audio rig").
# make rig STACK=elevenlabs [SUITE=smoke] [MAX_COST=5]   stacks: livekit, retell, elevenlabs,
# openai_realtime, gemini_live. make rig-report RUNS="evals/results/<a>.json evals/results/<b>.json"
rig:
	@test -n "$(STACK)" || { echo "usage: make rig STACK=<stack>"; exit 2; }
	uv run $(EVAL_ENV) python -m evals.audio_rig --stack $(STACK) $(if $(SUITE),--suite $(SUITE)) \
	  $(if $(MAX_COST),--max-cost $(MAX_COST))

rig-report:
	uv run python -m evals.audio_rig.report $(RUNS)

# Paid, cents: the side-model quick run on one question (evals/decision-models/PLAN.md section 6).
# make eval-decisions [SYSTEMS=rules,haiku,openai-decisions,jev]
eval-decisions:
	PYTHONPATH=. uv run $(EVAL_ENV) python evals/decision-models/run.py $(if $(SYSTEMS),--systems $(SYSTEMS))

# Re-run a recorded call's caller turns and diff the tool calls; exits 1 if they differ
replay:
	@test -n "$(CALL)" || { echo "usage: make replay CALL=<call id>"; exit 2; }
	uv run $(EVAL_ENV) python -m evals.replay $(CALL)

# Mirror a results file into LangSmith (optional viewer; does nothing without LANGSMITH_API_KEY).
# make langsmith                                  the newest file in evals/results
# make langsmith RUN=evals/results/<run>.json     a named one
langsmith:
	uv run $(EVAL_ENV) python -m evals.langsmith_sync $(RUN)

eval-upload: langsmith

# The mock ServiceTitan (see servicetitan_mock/README.md)
ST_PORT ?= 8101

seed-st:
	uv run python -m servicetitan_mock.seed

mock-st:
	@test -f data/servicetitan_mock.db || $(MAKE) seed-st
	uv run uvicorn servicetitan_mock.app:create_app --factory --port $(ST_PORT)

postman-st:
	sh scripts/run_postman_st.sh

# The tool server (docs/tools.md). It talks to the mock on ST_PORT unless FSM_BASE_URL says otherwise.
PORT ?= 8100

server:
	FSM_BASE_URL=$${FSM_BASE_URL:-http://127.0.0.1:$(ST_PORT)} DEBUG_TOKEN=$${DEBUG_TOKEN:-dev-debug-token} \
	  uv run uvicorn app.main:create_app --factory --port $(PORT)

postman:
	sh scripts/run_postman_tools.sh

# A call's timeline in plain text: make trace CALL=<call id> [DB=data/tool_server.db]
trace:
	@test -n "$(CALL)" || { echo "usage: make trace CALL=<call id>"; exit 2; }
	uv run python -m app.trace $(CALL) $(if $(DB),--db $(DB))

# A seeded day of calls on the owner view, served as if it were 5:20 PM that day:
# http://127.0.0.1:$(PORT)/owner?token=dev-owner-token
owner-demo:
	uv run python -m scripts.seed_owner_day --dir data/owner-demo
	TOOL_DB=data/owner-demo/tool.db OWNER_TOKEN=$${OWNER_TOKEN:-dev-owner-token} JOBS_INTERVAL_SECONDS=0 \
	  RULES_EDITORS=$${RULES_EDITORS:-Owner=dev-edit-code} \
	  FIXED_NOW=$$(cat data/owner-demo/now) \
	  uv run uvicorn app.main:create_app --factory --port $(PORT)

# Paid: the eval gate on every owner draft waiting for a check (evals/gate.py), about $0.39 for the
# 18 must-pass scenarios (MEASURED). make rules-gate [DB=data/tool_server.db]
rules-gate:
	uv run $(EVAL_ENV) python -m evals.gate --db $${DB:-data/tool_server.db}

# Paid: one scripted booked call, then the technician summary from the real post_call.summary model
# (scripts/summary_check.py), one model call.
summary-check:
	uv run $(EVAL_ENV) python -m scripts.summary_check

# The LiveKit voice worker (voice/livekit/README.md), against make server and make mock-st
voice-livekit:
	uv run --env-file .env python -m voice.livekit.worker dev

# Twilio trunk, LiveKit trunks and dispatch rule for the phone number (scripts/phone/setup.py)
phone-setup:
	uv run --env-file .env python -m scripts.phone.setup

# Copy, install and restart everything on the summit-air server: make deploy [ENV=path/to/.env]
deploy:
	sh deploy/summit-air/deploy.sh $(ENV)

# One real call to the agent's number from a simulated caller (a few cents)
test-call:
	uv run --env-file .env python -m voice.livekit.test_call

# The model's first token and prompt cache hits on a scripted call, no phone and no LiveKit (a few cents)
PROFILE ?= fast

measure-delay:
	uv run --env-file .env python -m voice.livekit.measure --profile $(PROFILE)

# The Retell baseline (voice/retell/README.md). The bridge's public URL defaults to the server.
RETELL_BRIDGE_URL ?= https://45-77-105-247.sslip.io

retell-push:
	RETELL_BRIDGE_URL=$(RETELL_BRIDGE_URL) uv run $(if $(wildcard .env),--env-file .env) \
	  python -m voice.retell.push $(if $(EVAL_URL),--eval-url $(EVAL_URL))

retell-bridge:
	uv run $(if $(wildcard .env),--env-file .env) uvicorn voice.retell.bridge:create_app --factory --port 8102

retell-deploy:
	sh deploy/retell/deploy.sh

retell-report:
	uv run python -m voice.retell.report $(if $(DB),--db $(DB))
