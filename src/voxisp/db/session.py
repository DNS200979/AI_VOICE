"""Engine/sessão SQLAlchemy async — Postgres em produção, SQLite em testes.

Não há estado de módulo global aqui de propósito: `build_session_maker()`
cria um engine+sessionmaker novos a cada chamada, para que testes possam
apontar para `sqlite+aiosqlite:///:memory:` sem interferir com a instância
usada pela API (que aponta para `settings.database_url`, Postgres).
"""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from voxisp.db.models import Base


class MigrationsNotAppliedError(Exception):
    """`alembic upgrade head` ainda não rodou contra este banco — ver
    `check_migrations_applied`."""


def build_session_maker(database_url: str, *, echo: bool = False) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, echo=echo)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_maker


async def init_models(engine: AsyncEngine) -> None:
    """`create_all` de conveniência — só para testes (SQLite in-memory).

    Nunca use isso contra o Postgres de produção: aplique `alembic upgrade
    head` como passo de deploy separado (`check_migrations_applied` abaixo
    é o que o boot da API real usa para falhar cedo e com uma mensagem
    clara se isso não tiver rodado, em vez de criar tabelas por baixo dos
    panos ou explodir com um erro genérico de "relation does not exist").
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def check_migrations_applied(engine: AsyncEngine) -> None:
    """Falha cedo e com mensagem clara se `alembic upgrade head` ainda não
    rodou — em vez de deixar a primeira query real da aplicação estourar
    um `relation "call" does not exist` genérico, ou (pior) criar o schema
    silenciosamente via `create_all` no boot (perigoso com múltiplas
    réplicas subindo ao mesmo tempo, spec §5)."""
    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    if "alembic_version" not in table_names or not Base.metadata.tables.keys() <= set(table_names):
        raise MigrationsNotAppliedError(
            "Schema do banco desatualizado ou ausente — rode `alembic upgrade head` "
            "antes de subir a API com PERSISTENCE_ENABLED=true."
        )
