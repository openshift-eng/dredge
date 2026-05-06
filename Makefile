.PHONY: lint format typecheck test check

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src/dredge/

test:
	uv run pytest

check: lint typecheck test
