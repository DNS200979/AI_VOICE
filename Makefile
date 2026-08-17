.PHONY: install dev test lint up down migrate migration

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

# Aplica as migrações pendentes (Alembic, ver src/voxisp/db/migrations/).
# Passo de deploy separado do boot da API — com PERSISTENCE_ENABLED=true, a
# API recusa subir se isto não tiver rodado (voxisp.db.session.check_migrations_applied).
migrate:
	.venv/bin/alembic upgrade head

# Gera uma nova revisão via autogenerate, a partir do diff entre
# src/voxisp/db/models.py (Base.metadata) e o estado atual do banco
# apontado por DATABASE_URL. Sempre revisar o arquivo gerado antes de
# commitar — autogenerate não detecta tudo (renomes de coluna, mudanças de
# tipo em alguns dialetos etc.).
migration:
	.venv/bin/alembic revision --autogenerate -m "$(m)"
