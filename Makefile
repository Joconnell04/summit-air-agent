.PHONY: setup test lint check ratchet ratchet-update seed-st mock-st postman-st

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

# The mock ServiceTitan (see servicetitan_mock/README.md)
ST_PORT ?= 8101

seed-st:
	uv run python -m servicetitan_mock.seed

mock-st:
	@test -f data/servicetitan_mock.db || $(MAKE) seed-st
	uv run uvicorn servicetitan_mock.app:create_app --factory --port $(ST_PORT)

postman-st:
	sh scripts/run_postman_st.sh
