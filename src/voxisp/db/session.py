"""Engine/sessão SQLAlchemy async — Postgres em produção, SQLite em testes.

Não há estado de módulo global aqui de propósito: `build_session_maker()`
cria um engine+sessionmaker novos a cada chamada, para que testes possam
apontar para `sqlite+aiosqlite:///:memory:` sem interferir com a instância
usada pela API (que aponta para `settings.database_url`, Postgres).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from voxisp.db.models import Base


def build_session_maker(database_url: str, *, echo: bool = False) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, echo=echo)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_maker


async def init_models(engine: AsyncEngine) -> None:
    """Cria as tabelas se não existirem — atalho de dev/teste.

    Em produção, prefira aplicar `db/schema.sql` (ou uma migração real, ex.
    Alembic) em vez de depender de `create_all` no boot da aplicação.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
