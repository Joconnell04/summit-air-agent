.PHONY: setup test lint check ratchet ratchet-update eval-upload seed-st mock-st postman-st server postman trace \
	voice-livekit phone-setup deploy test-call

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

# Mirror a results file into LangSmith (optional viewer; does nothing without LANGSMITH_API_KEY)
eval-upload:
	@test -n "$(RUN)" || { echo "usage: make eval-upload RUN=evals/results/<run>.json"; exit 2; }
	uv run $(if $(wildcard .env),--env-file .env) python -m evals.langsmith_sync $(RUN)

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

# The LiveKit voice worker (voice/livekit/README.md), against make server and make mock-st
voice-livekit:
	uv run --env-file .env python -m voice.livekit.worker dev

# Twilio trunk, LiveKit trunks and dispatch rule for the phone number (scripts/phone/setup.py)
phone-setup:
	uv run --env-file .env python -m scripts.phone.setup

# Copy, install and restart everything on the summit-air server: make deploy [ENV=path/to/.env]
deploy:
	sh deploy/summit-air/deploy.sh $(or $(ENV),.env)

# One real call to the agent's number from a simulated caller (a few cents)
test-call:
	uv run --env-file .env python -m voice.livekit.test_call
