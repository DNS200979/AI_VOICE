"""Repositório de persistência — grava call/turn/action/escalation/incident_link
(spec §8) a partir dos pontos de gravação do `CallOrchestrator`.

Cada método abre e comita sua própria sessão a partir do `async_sessionmaker`
injetado, então turnos de chamadas diferentes não competem por uma sessão
compartilhada. Não é o caminho de maior throughput possível (uma sessão por
INSERT), mas é simples e correto — otimizar (batch de turnos, sessão por
chamada) é trabalho de quando o volume justificar.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voxisp.db.models import Action, Call, Escalation, IncidentLink, Turn


class CallRepository:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def create_call(
        self, *, ani: str, dnis: str, tenant_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        call = Call(ani=ani, dnis=dnis, tenant_id=tenant_id)
        async with self._session_maker() as session:
            session.add(call)
            await session.commit()
            await session.refresh(call)
            return call.id

    async def add_turn(
        self,
        *,
        call_id: uuid.UUID,
        seq: int,
        role: str,
        transcript: str,
        intent: str | None = None,
        entities: dict | None = None,
        latency_ms: dict | None = None,
        tokens: dict | None = None,
        asr_confidence: float | None = None,
    ) -> None:
        turn = Turn(
            call_id=call_id,
            seq=seq,
            role=role,
            transcript=transcript,
            intent=intent,
            entities=entities or {},
            latency_ms=latency_ms or {},
            tokens=tokens or {},
            asr_confidence=asr_confidence,
        )
        async with self._session_maker() as session:
            session.add(turn)
            await session.commit()

    async def add_action(
        self,
        *,
        call_id: uuid.UUID,
        tool_name: str,
        params: dict,
        result: dict,
        status: str,
        idempotency_key: str,
    ) -> None:
        action = Action(
            call_id=call_id,
            tool_name=tool_name,
            params=params,
            result=result,
            status=status,
            idempotency_key=idempotency_key,
        )
        async with self._session_maker() as session:
            session.add(action)
            await session.commit()

    async def add_escalation(
        self, *, call_id: uuid.UUID, reason: str, queue: str | None, context_payload: dict
    ) -> None:
        escalation = Escalation(call_id=call_id, reason=reason, queue=queue, context_payload=context_payload)
        async with self._session_maker() as session:
            session.add(escalation)
            await session.commit()

    async def link_incident(
        self, *, call_id: uuid.UUID, incident_id: str, source: str = "massivo"
    ) -> None:
        link = IncidentLink(call_id=call_id, incident_id=incident_id, source=source)
        async with self._session_maker() as session:
            session.add(link)
            await session.commit()

    async def finish_call(
        self,
        *,
        call_id: uuid.UUID,
        outcome: str,
        contained: bool,
        subscriber_id: str | None = None,
        escalated_to: str | None = None,
        protocol: str | None = None,
    ) -> None:
        async with self._session_maker() as session:
            result = await session.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one()
            call.ended_at = datetime.now(UTC)
            started = call.started_at
            if started is not None:
                if started.tzinfo is None:  # SQLite não preserva tzinfo em CURRENT_TIMESTAMP
                    started = started.replace(tzinfo=UTC)
                call.duration_s = int((call.ended_at - started).total_seconds())
            call.outcome = outcome
            call.contained = contained
            if subscriber_id is not None:
                call.subscriber_id = subscriber_id
            if escalated_to is not None:
                call.escalated_to = escalated_to
            if protocol is not None:
                call.protocol = protocol
            await session.commit()

    async def get_call(self, call_id: uuid.UUID) -> Call:
        async with self._session_maker() as session:
            result = await session.execute(select(Call).where(Call.id == call_id))
            return result.scalar_one()
