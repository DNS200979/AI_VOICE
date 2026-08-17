"""Testes do parser determinístico de data/hora em português (OPS-02
reagendamento sem LLM real, ver README item 2 do roadmap)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from voxisp.orchestrator.pt_datetime import parse_visit_window

_TZ = ZoneInfo("America/Sao_Paulo")
# Fixo numa sexta-feira, para os testes de dia da semana serem determinísticos.
_FRIDAY = datetime(2026, 8, 14, 10, 0, tzinfo=_TZ)


def test_amanha_de_manha():
    result = parse_visit_window("quero remarcar para amanhã de manhã", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-08-15"
    assert result.start.hour == 8
    assert result.end.hour == 12


def test_depois_de_amanha_a_tarde():
    result = parse_visit_window("pode ser depois de amanhã à tarde", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-08-16"
    assert result.start.hour == 13


def test_hoje_a_noite():
    result = parse_visit_window("consigo hoje à noite", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-08-14"
    assert result.start.hour == 18


def test_dia_da_semana_futuro():
    # sexta -> "segunda" deve cair na próxima segunda (17/08), não passada.
    result = parse_visit_window("prefiro segunda de manhã", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-08-17"


def test_dia_da_semana_dito_no_proprio_dia_pula_para_proxima_semana():
    result = parse_visit_window("pode ser sexta de tarde", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-08-21"  # não 14/08 (hoje)


def test_explicit_time_with_colon():
    result = parse_visit_window("quero remarcar para amanhã às 14:30", now=_FRIDAY)
    assert result is not None
    assert result.start.hour == 14
    assert result.start.minute == 30
    assert result.end.hour == 15


def test_explicit_time_with_h():
    result = parse_visit_window("dia 20 às 9h", now=_FRIDAY)
    assert result is not None
    assert result.start.hour == 9
    assert result.start.date().isoformat() == "2026-08-20"


def test_date_slash_format():
    result = parse_visit_window("dia 01/09 de manhã", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-09-01"


def test_dia_com_mes_nomeado():
    result = parse_visit_window("dia 5 de setembro à tarde", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-09-05"


def test_dia_sem_mes_no_passado_pula_para_proximo_mes():
    # "hoje" é 14/08 — "dia 3" sem mês, sozinho, seria 03/08 (passado) -> vira 03/09.
    result = parse_visit_window("dia 3 de manhã", now=_FRIDAY)
    assert result is not None
    assert result.start.date().isoformat() == "2026-09-03"


def test_apenas_dia_sem_periodo_nao_parseia():
    assert parse_visit_window("amanhã", now=_FRIDAY) is None


def test_apenas_periodo_sem_dia_nao_parseia():
    assert parse_visit_window("de manhã", now=_FRIDAY) is None


def test_frase_sem_nenhum_dado_nao_parseia():
    assert parse_visit_window("pode ser o quanto antes", now=_FRIDAY) is None
