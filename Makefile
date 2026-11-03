.PHONY: setup test lint check ratchet ratchet-update eval replay eval-upload seed-st mock-st postman-st server postman trace

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

# Paid: runs scenarios against a stack (evals/README.md). The smoke suite is about $1 (ASSUMED).
# make eval SUITE=smoke | TAGS=flow:safety,difficulty:rigorous | STACKS=livekit,retell | PROFILES=fast
ENV_FILE ?= .env
EVAL_ENV = $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE))

eval:
	uv run $(EVAL_ENV) python -m evals.run $(if $(SUITE),--suite $(SUITE)) $(if $(TAGS),--tags $(TAGS)) \
	  $(if $(STACKS),--stacks $(STACKS)) $(if $(PROFILES),--profiles $(PROFILES)) \
	  $(if $(SAMPLES),--samples $(SAMPLES))

# Re-run a recorded call's caller turns and diff the tool calls; exits 1 if they differ
replay:
	@test -n "$(CALL)" || { echo "usage: make replay CALL=<call id>"; exit 2; }
	uv run $(EVAL_ENV) python -m evals.replay $(CALL)

# Mirror a results file into LangSmith (optional viewer; does nothing without LANGSMITH_API_KEY)
eval-upload:
	@test -n "$(RUN)" || { echo "usage: make eval-upload RUN=evals/results/<run>.json"; exit 2; }
	uv run $(EVAL_ENV) python -m evals.langsmith_sync $(RUN)

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
