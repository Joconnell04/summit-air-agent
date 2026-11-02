.PHONY: setup test lint check ratchet ratchet-update

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
