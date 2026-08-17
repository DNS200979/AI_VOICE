"""Interface de ASR (Speech-to-Text) — spec §4.1.

Candidatos de produção: Deepgram Nova (cloud, melhor custo/latência) — a
spec lista primeiro, e é o único implementado de verdade aqui. Azure
Speech, NVIDIA Riva/Parakeet e Whisper large-v3 fine-tuned continuam como
candidatos não implementados (on-prem, para quando o ISP exigir dado
local). `StubASR` existe para dev/CI sem credenciais de nuvem.

`DeepgramASR` — endpoints e formatos verificados em:
- https://developers.deepgram.com/reference/listen-live (API reference,
  não o guia com exemplos via SDK — a query params/mensagens JSON exatas
  só estão documentadas ali)
- https://developers.deepgram.com/reference/authentication
Ver docs/voice/deepgram.md para o mapeamento completo e limitações.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode


@dataclass(frozen=True)
class ASRResult:
    text: str
    is_final: bool
    confidence: float


class ASREngine(Protocol):
    """Contrato de streaming: recebe frames PCM 8kHz, produz resultados parciais/finais."""

    async def stream(self, audio_frames: AsyncIterator[bytes]) -> AsyncIterator[ASRResult]: ...


class StubASR:
    """Implementação de desenvolvimento: não reconhece áudio de verdade.

    Útil para testar a FSM/orquestrador via texto direto (bypassa o
    reconhecimento). Plugue `DeepgramASR`/`AzureASR`/`RivaASR` reais
    seguindo o mesmo `Protocol` antes de ir a produção.
    """

    async def stream(self, audio_frames: AsyncIterator[bytes]) -> AsyncIterator[ASRResult]:
        async for _ in audio_frames:
            yield ASRResult(text="", is_final=False, confidence=0.0)


class DeepgramNotConfiguredError(Exception):
    """`ASR_API_KEY` ausente do `.env` (com `ASR_PROVIDER=deepgram`)."""


def _default_connect(url: str, headers: dict[str, str]) -> Any:
    """`websockets.connect()` devolve um async context manager — só
    importa a lib quando de fato vai conectar (Deepgram é opt-in via
    `ASR_PROVIDER=deepgram`, spec §4.1)."""
    import websockets

    return websockets.connect(url, additional_headers=headers)


class DeepgramASR:
    """ASR real via WebSocket de streaming da Deepgram (Nova-3) — spec §4.1.

    Particularidades reais que moldaram este código (verificadas na API
    reference, não no guia de exemplos via SDK):

    - Autenticação é por header (`Authorization: Token <api_key>`), não
      query param — a doc dos SDKs abstrai isso, mas a referência confirma
      o header. Nunca vaza a API key na URL/logs de acesso.
    - `nova-3` tem suporte dedicado a `pt-BR` (Nova-2 só tinha
      multilíngue limitado a inglês↔espanhol) — é o modelo padrão aqui,
      não `nova-2`.
    - `smart_format=true` cobre parte do requisito de ITN do §4.1 (números
      por extenso → dígitos), mas não é validado aqui contra CPF
      especificamente — isso continua sendo `_validate_cpf` no
      orquestrador, camada separada de propósito.
    - Fechamento gracioso: manda `{"type": "CloseStream"}` quando
      `audio_frames` acaba, em vez de só derrubar o socket — dá tempo da
      Deepgram devolver o resultado final pendente.
    - **Não implementado**: `KeepAlive` (a Deepgram fecha a conexão após
      ~12s sem áudio) — assume que `audio_frames` nunca para de produzir
      frames enquanto a chamada estiver ativa (o caso real de telefonia);
      documentado como limitação em docs/voice/deepgram.md.
    """

    def __init__(
        self,
        api_key: str,
        *,
        language: str = "pt-BR",
        model: str = "nova-3",
        sample_rate: int = 8000,
        encoding: str = "linear16",
        connect_fn: Callable[[str, dict[str, str]], Any] | None = None,
    ) -> None:
        if not api_key:
            raise DeepgramNotConfiguredError(
                "ASR_API_KEY não configurado. Ver docs/voice/deepgram.md."
            )
        self._api_key = api_key
        self._language = language
        self._model = model
        self._sample_rate = sample_rate
        self._encoding = encoding
        self._connect_fn = connect_fn or _default_connect

    def _url(self) -> str:
        params = {
            "model": self._model,
            "language": self._language,
            "encoding": self._encoding,
            "sample_rate": str(self._sample_rate),
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
            # Ajuda no ITN de números (spec §4.1) — CPF/protocolo continuam
            # validados/parseados no orquestrador, não confiamos só nisso.
            "smart_format": "true",
            # Latência de finalização alvo <=300ms (spec §4.1) — endpointing
            # em ms de silêncio antes de marcar is_final=true.
            "endpointing": "300",
        }
        return f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"

    async def stream(self, audio_frames: AsyncIterator[bytes]) -> AsyncIterator[ASRResult]:
        headers = {"Authorization": f"Token {self._api_key}"}
        async with self._connect_fn(self._url(), headers) as ws:
            pump = asyncio.ensure_future(self._pump_audio(ws, audio_frames))
            try:
                async for raw in ws:
                    result = self._parse_message(raw)
                    if result is not None:
                        yield result
            finally:
                pump.cancel()
                # asyncio.CancelledError herda de BaseException (desde
                # 3.8), não de Exception — confirmado rodando os testes
                # (contextlib.suppress(Exception) não pegava, o cancel()
                # vazava e derrubava o generator inteiro).
                with contextlib.suppress(asyncio.CancelledError):
                    await pump

    @staticmethod
    async def _pump_audio(ws: Any, audio_frames: AsyncIterator[bytes]) -> None:
        async for frame in audio_frames:
            await ws.send(frame)
        # Fim do áudio (ex.: fim da chamada) — pede o resultado final
        # pendente em vez de só fechar o socket.
        await ws.send(json.dumps({"type": "CloseStream"}))

    @staticmethod
    def _parse_message(raw: str | bytes) -> ASRResult | None:
        data = json.loads(raw)
        if data.get("type") != "Results":
            return None
        alternatives = (data.get("channel") or {}).get("alternatives") or []
        if not alternatives:
            return None
        transcript = alternatives[0].get("transcript", "")
        if not transcript:
            # Deepgram manda Results vazios em trechos de silêncio — nunca
            # produz um ASRResult com texto vazio (o StubASR faz isso de
            # propósito só porque é stub; aqui seria ruído para a FSM).
            return None
        return ASRResult(
            text=transcript,
            is_final=bool(data.get("is_final", False)),
            confidence=float(alternatives[0].get("confidence", 0.0)),
        )


def get_asr_engine(provider: str = "stub", settings: Any = None) -> ASREngine:
    """Fábrica de motores de ASR — mesmo padrão de `get_llm_client`/`get_connector`."""
    if provider == "stub":
        return StubASR()
    if provider == "deepgram":
        if settings is None:
            from voxisp.config import settings as default_settings

            settings = default_settings
        return DeepgramASR(
            api_key=settings.asr_api_key,
            language=settings.asr_language,
            sample_rate=settings.asr_sample_rate,
            encoding=settings.asr_encoding,
        )
    raise ValueError(f"ASR_PROVIDER '{provider}' desconhecido. Disponíveis: stub, deepgram.")
