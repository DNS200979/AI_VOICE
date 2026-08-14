"""Modelos ORM (SQLAlchemy 2.0 async) — espelham `db/schema.sql` (spec §8).

Divergência intencional do `schema.sql`: `Call.tenant_id` aqui não tem FK
obrigatória para `tenant_config` — multi-tenant real (com seed de tenant e
RLS no Postgres, spec §5) é trabalho futuro. `schema.sql` continua sendo a
referência "fiel à spec"; este módulo é a camada de aplicação que já
funciona hoje, inclusive em SQLite (usado nos testes, sem exigir Postgres).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, SmallInteger, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TenantConfig(Base):
    __tablename__ = "tenant_config"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    connectors: Mapped[dict] = mapped_column(JSON, default=dict)
    persona: Mapped[dict] = mapped_column(JSON, default=dict)
    sla: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Call(Base):
    __tablename__ = "call"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    ani: Mapped[str] = mapped_column(String, nullable=False)
    dnis: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subscriber_id: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    contained: Mapped[bool] = mapped_column(default=False)
    escalated_to: Mapped[str | None] = mapped_column(String, nullable=True)
    protocol: Mapped[str | None] = mapped_column(String, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String, nullable=True)
    csat: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    turns: Mapped[list[Turn]] = relationship(back_populates="call", cascade="all, delete-orphan")
    actions: Mapped[list[Action]] = relationship(back_populates="call", cascade="all, delete-orphan")
    escalations: Mapped[list[Escalation]] = relationship(back_populates="call", cascade="all, delete-orphan")


class Turn(Base):
    __tablename__ = "turn"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # customer | assistant
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    asr_confidence: Mapped[float | None] = mapped_column(nullable=True)
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    entities: Mapped[dict] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[dict] = mapped_column(JSON, default=dict)
    tokens: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    call: Mapped[Call] = relationship(back_populates="turns")


class Action(Base):
    __tablename__ = "action"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False)  # pending | success | failed
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    call: Mapped[Call] = relationship(back_populates="actions")


class Escalation(Base):
    __tablename__ = "escalation"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    queue: Mapped[str | None] = mapped_column(String, nullable=True)
    context_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    call: Mapped[Call] = relationship(back_populates="escalations")


class IncidentLink(Base):
    __tablename__ = "incident_link"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    incident_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "massivo"
