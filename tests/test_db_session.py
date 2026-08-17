"""Testes de `check_migrations_applied` — o boot da API real usa isso para
falhar cedo, com mensagem clara, se `alembic upgrade head` não rodou (spec
§5: nunca criar/alterar schema sozinho no boot, perigoso com múltiplas
réplicas). Ver src/voxisp/db/migrations/ para as migrações de verdade.
"""
import pytest
from sqlalchemy import text

from voxisp.db.models import Base
from voxisp.db.session import (
    MigrationsNotAppliedError,
    build_session_maker,
    check_migrations_applied,
    init_models,
)


async def test_raises_when_schema_completely_missing():
    engine, _ = build_session_maker("sqlite+aiosqlite:///:memory:")
    try:
        with pytest.raises(MigrationsNotAppliedError):
            await check_migrations_applied(engine)
    finally:
        await engine.dispose()


async def test_raises_when_tables_exist_but_alembic_version_missing():
    """Alguém rodou `create_all` direto (ex.: copiou init_models por
    engano) sem nunca passar pelo Alembic — ainda tem que falhar, porque
    não há garantia de que o schema bate com a migração mais recente."""
    engine, _ = build_session_maker("sqlite+aiosqlite:///:memory:")
    try:
        await init_models(engine)  # cria as tabelas de Base.metadata, sem alembic_version
        with pytest.raises(MigrationsNotAppliedError):
            await check_migrations_applied(engine)
    finally:
        await engine.dispose()


async def test_passes_when_tables_and_alembic_version_present():
    engine, _ = build_session_maker("sqlite+aiosqlite:///:memory:")
    try:
        await init_models(engine)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await check_migrations_applied(engine)  # não levanta
    finally:
        await engine.dispose()


async def test_raises_when_some_app_table_is_missing_despite_alembic_version():
    """Migração parcial/corrompida: alembic_version existe mas nem toda
    tabela de Base.metadata foi criada — ainda é um estado inconsistente."""
    engine, _ = build_session_maker("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            # Só a primeira tabela de Base.metadata, de propósito.
            first_table = next(iter(Base.metadata.tables.values()))
            await conn.run_sync(lambda sync_conn: first_table.create(sync_conn))
            await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        with pytest.raises(MigrationsNotAppliedError):
            await check_migrations_applied(engine)
    finally:
        await engine.dispose()
