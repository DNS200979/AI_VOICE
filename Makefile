.PHONY: install dev test lint up down

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

dev:
	.venv/bin/uvicorn voxisp.main:app --reload --app-dir src

test:
	.venv/bin/pytest -v

lint:
	.venv/bin/ruff check src tests

up:
	docker compose up -d

down:
	docker compose down
