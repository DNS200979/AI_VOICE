"""Parser determinístico de data/hora em português coloquial.

Existe para o slot-filling de reagendamento de visita técnica (OPS-02)
quando não há LLM real configurado (`LLM_PROVIDER != anthropic`) — sem
isso, `_handle_ops_02` não tinha como extrair "para quando" o cliente quer
remarcar sem escalar sempre (ver README, item 2 do roadmap pendente).

Não é um parser de linguagem natural genérico — cobre deliberadamente só
um subconjunto de expressões comuns em ligação de call center de ISP
brasileiro. Regra central (spec §12: nunca inventar um dado que o cliente
não confirmou): só devolve uma janela quando encontra COM CONFIANÇA um DIA
(relativo, dia da semana, ou data explícita) E um PERÍODO/HORÁRIO
(manhã/tarde/noite ou hora explícita) na mesma fala — só dia ou só horário
não é suficiente, `parse_visit_window` devolve `None` e quem chama decide
o que fazer (tipicamente: perguntar de novo, contando como falha de
reconhecimento do slot — regra §7.1 #3).

Assume fuso horário do Brasil (`America/Sao_Paulo`) por padrão — o público
do produto inteiro é ISP brasileiro (CPF, Decreto 11.034/SAC, RGC/Anatel).
Se o provedor operar num fuso diferente, passe `now` já ajustado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Sao_Paulo")

_WEEKDAYS: dict[str, int] = {
    "segunda-feira": 0, "segunda": 0,
    "terça-feira": 1, "terca-feira": 1, "terça": 1, "terca": 1,
    "quarta-feira": 2, "quarta": 2,
    "quinta-feira": 3, "quinta": 3,
    "sexta-feira": 4, "sexta": 4,
    "sábado": 5, "sabado": 5,
    "domingo": 6,
}

_PERIODS: dict[str, tuple[time, time]] = {
    "manhã": (time(8, 0), time(12, 0)),
    "manha": (time(8, 0), time(12, 0)),
    "meio-dia": (time(12, 0), time(13, 0)),
    "meio dia": (time(12, 0), time(13, 0)),
    "tarde": (time(13, 0), time(18, 0)),
    "noite": (time(18, 0), time(20, 0)),
}

_MONTHS: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}

_TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:h](\d{2})?\b")
_DATE_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_DIA_RE = re.compile(r"\bdia\s+(\d{1,2})(?:\s+de\s+(\w+))?\b")


@dataclass
class ParsedVisitWindow:
    start: datetime
    end: datetime


def _find_day(lowered: str, today: date) -> date | None:
    if "depois de amanhã" in lowered or "depois de amanha" in lowered:
        return today + timedelta(days=2)
    if "amanhã" in lowered or "amanha" in lowered:
        return today + timedelta(days=1)
    if "hoje" in lowered:
        return today

    match = _DATE_SLASH_RE.search(lowered)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None

    match = _DIA_RE.search(lowered)
    if match:
        day = int(match.group(1))
        month = _MONTHS.get(match.group(2) or "", today.month)
        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None
        if candidate < today:
            # "dia 3" dito em 20/08 quase sempre significa o dia 3 do
            # próximo mês (ou ano, se for dezembro) — não uma data passada.
            try:
                candidate = date(today.year, month + 1, day) if month < 12 else date(today.year + 1, 1, day)
            except ValueError:
                return None
        return candidate

    for name, weekday in _WEEKDAYS.items():
        if _contains_word(lowered, name):
            delta = (weekday - today.weekday()) % 7
            delta = delta or 7  # dito no próprio dia da semana = a próxima ocorrência, não hoje
            return today + timedelta(days=delta)

    return None


def _contains_word(lowered: str, phrase: str) -> bool:
    """Match de fronteira de palavra, não substring solta — "manhã" É
    substring de "amanhã" ("a" + "manhã"), então `"manhã" in "amanhã"`
    (achado rodando os testes) faz "amanhã" sozinho ser lido como se
    tivesse um período embutido. `\\b` resolve porque não há fronteira de
    palavra entre o "a" e o "m" de "amanhã"."""
    return re.search(rf"\b{re.escape(phrase)}\b", lowered) is not None


def _find_window(lowered: str) -> tuple[time, time] | None:
    match = _TIME_RE.search(lowered)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        if 0 <= minute <= 59:
            start = time(hour, minute)
            end = (datetime.combine(date(2000, 1, 1), start) + timedelta(hours=1)).time()
            return start, end
    for name, window in _PERIODS.items():
        if _contains_word(lowered, name):
            return window
    return None


def parse_visit_window(utterance: str, *, now: datetime | None = None) -> ParsedVisitWindow | None:
    """`None` se não conseguir identificar um dia E um período/horário com
    confiança na mesma fala — nunca chuta uma janela que o cliente não
    disse (spec §12)."""
    now = now or datetime.now(_TZ)
    lowered = utterance.lower()

    day = _find_day(lowered, now.date())
    if day is None:
        return None
    window = _find_window(lowered)
    if window is None:
        return None

    start_time, end_time = window
    tzinfo = now.tzinfo or _TZ
    start = datetime.combine(day, start_time, tzinfo=tzinfo)
    end = datetime.combine(day, end_time, tzinfo=tzinfo)
    return ParsedVisitWindow(start=start, end=end)
