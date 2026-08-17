"""API HTTP de demonstração do orquestrador — não é o runtime de voz final.

Serve para exercitar identificação + intents + transbordo via texto,
sem telefonia/ASR/TTS reais plugados (ver spec §3 para a arquitetura
completa de voz). Útil para QA do FSM/Connector Hub e para o loop de
regressão do §9 antes de existir um voice runtime real (LiveKit/Pipecat).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from voxisp.config import settings
from voxisp.connectors import get_connector
from voxisp.db.repository import CallRepository
from voxisp.db.session import build_session_maker, check_migrations_applied
from voxisp.observability.logging import configure_logging, get_logger
from voxisp.orchestrator import CallOrchestrator, get_llm_client, get_tool_executor

configure_logging()
logger = get_logger(__name__)

_SESSIONS: dict[str, CallOrchestrator] = {}
_repository: CallRepository | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _repository
    if settings.persistence_enabled:
        engine, session_maker = build_session_maker(settings.database_url)
        # O boot NUNCA cria/altera schema sozinho (perigoso com múltiplas
        # réplicas subindo ao mesmo tempo) — só confirma que `alembic
        # upgrade head` já rodou (passo de deploy separado, ver
        # alembic.ini/db/migrations/) e falha cedo, com mensagem clara, se
        # não tiver rodado.
        await check_migrations_applied(engine)
        _repository = CallRepository(session_maker)
        logger.info("persistence_enabled")
    yield


app = FastAPI(
    title="VOX-ISP",
    description="Atendente virtual de voz para ISPs — API de orquestração (spec-voicebot-isp.md)",
    version="0.1.0",
    lifespan=lifespan,
)


class IdentifyRequest(BaseModel):
    cpf: str


class UtteranceRequest(BaseModel):
    text: str


class TurnResponse(BaseModel):
    text: str
    escalate: bool = False
    protocol_number: str | None = None
    escalation_reason: str | None = None


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "isp_connector": settings.isp_connector,
        "llm_provider": settings.llm_provider,
        "tool_calling_enabled": settings.llm_provider == "anthropic",
        "persistence_enabled": settings.persistence_enabled,
    }


@app.post("/calls", response_model=TurnResponse)
async def start_call() -> TurnResponse:
    call_id = str(uuid.uuid4())
    connector = get_connector(settings.isp_connector)
    orchestrator = CallOrchestrator(
        connector=connector,
        llm=get_llm_client(settings.llm_provider, settings),
        # Tool-calling real (§3.2/§4.3) — None quando LLM_PROVIDER != "anthropic",
        # os handlers caem de volta para chamada direta ao conector.
        tool_executor=get_tool_executor(settings.llm_provider, settings, connector),
        repository=_repository,
        ani="webapi",
        dnis="demo",
    )
    _SESSIONS[call_id] = orchestrator
    result = await orchestrator.greet()
    logger.info("call_started", call_id=call_id)
    return TurnResponse(text=f"[{call_id}] {result.text}")


def _get_session(call_id: str) -> CallOrchestrator:
    session = _SESSIONS.get(call_id)
    if session is None:
        raise HTTPException(status_code=404, detail="call_id não encontrado ou expirado")
    return session


@app.post("/calls/{call_id}/identify", response_model=TurnResponse)
async def identify(call_id: str, body: IdentifyRequest) -> TurnResponse:
    session = _get_session(call_id)
    result = await session.identify(body.cpf)
    return TurnResponse(
        text=result.text,
        escalate=result.escalate,
        protocol_number=result.protocol_number,
        escalation_reason=result.escalation.reason if result.escalation else None,
    )


@app.post("/calls/{call_id}/utterance", response_model=TurnResponse)
async def utterance(call_id: str, body: UtteranceRequest) -> TurnResponse:
    session = _get_session(call_id)
    result = await session.handle_utterance(body.text)
    if result.escalate:
        logger.info("call_escalated", call_id=call_id, reason=result.escalation.reason if result.escalation else None)
        _SESSIONS.pop(call_id, None)
    return TurnResponse(
        text=result.text,
        escalate=result.escalate,
        protocol_number=result.protocol_number,
        escalation_reason=result.escalation.reason if result.escalation else None,
    )


@app.post("/calls/{call_id}/end")
async def end_call(call_id: str) -> dict:
    """Encerra a chamada sem transbordo (contida) — equivalente ao hangup em
    telefonia real. Chamadas já escaladas já são finalizadas/removidas em
    `/utterance`; este endpoint cobre o caso de sucesso (ver `CallOrchestrator.finish`)."""
    session = _get_session(call_id)
    await session.finish()
    _SESSIONS.pop(call_id, None)
    logger.info("call_ended", call_id=call_id)
    return {"status": "ended", "call_id": call_id}
