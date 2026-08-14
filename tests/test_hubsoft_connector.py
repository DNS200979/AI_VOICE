"""Testes do HubsoftConnector real — sem rede: usa `httpx.MockTransport`
simulando as respostas documentadas em docs.hubsoft.com.br /
github.com/hubsoftbrasil/api (payloads copiados dos exemplos oficiais).
"""
import json as _json
from datetime import date

import httpx
import pytest

from voxisp.config import Settings
from voxisp.connectors import get_connector
from voxisp.connectors.base import ConnectorError, ISPConnector
from voxisp.connectors.hubsoft import (
    HubsoftConnector,
    HubsoftNotConfiguredError,
    _infer_invoice_status,
    _map_so_status,
    _mask_cpf,
    _parse_br_date,
    _parse_radius_datetime,
)
from voxisp.connectors.models import InvoiceStatus, ServiceOrderStatus, SODraft

AUTH_RESPONSE = {
    "access_token": "fake-access-token",
    "expires_in": 2592000,
    "refresh_token": "fake-refresh-token",
    "token_type": "Bearer",
}

# Payload real de exemplo — clientes/consulta.rst
CLIENTE_RESPONSE = {
    "status": "success",
    "msg": "Dados consultados com sucesso",
    "clientes": [
        {
            "id_cliente": 11201,
            "codigo_cliente": 421,
            "nome_razaosocial": "GUILHERME SILVA",
            "cpf_cnpj": "10682083681",
            "telefone_primario": "37988242968",
            "email_principal": "guilherme@silva.com.br",
            "data_cadastro": "2017-08-05 00:00:00",
            "servicos": [
                {
                    "id_cliente_servico": 11201,
                    "nome": "4M",
                    "valor": 119.9,
                    "status": "Serviço Habilitado",
                    "login": "guilhermesilva1068",
                    "interface": {"nome": "PON5", "tipo": "gpon"},
                    "endereco_instalacao": {"completo": "RUA MINAS GERAIS, 1793 - IPIRANGA"},
                }
            ],
        }
    ],
}

# Payload real de exemplo — clientes/financeiro.rst (um pago, um em aberto)
FATURAS_RESPONSE = {
    "status": "success",
    "msg": "Dados consultados com sucesso",
    "faturas": [
        {
            "id_fatura": 36397,
            "valor": 54.95,
            "data_vencimento": "10/10/2017",
            "data_pagamento": "31/07/2017",
            "linha_digitavel": None,
            "pix_copia_cola": None,
        },
        {
            "id_fatura": 36403,
            "valor": 10,
            "data_vencimento": "09/11/2099",
            "data_pagamento": None,
            "linha_digitavel": "75691.31662 01006.726101 27210.000017 7 73380000001000",
            "pix_copia_cola": "00020101026216880014BR.GOV.BCB.PIX...",
        },
    ],
}

# Payload real de exemplo — clientes/ordem_servico.rst
ORDENS_SERVICO_RESPONSE = {
    "status": "success",
    "msg": "Dados consultados com sucesso",
    "ordens_servico": [
        {
            "id_ordem_servico": 78,
            "numero_ordem_servico": "74",
            "data_cadastro": "12/04/2018 15:26:28",
            "tipo": "SUPORTE",
            "data_inicio_programado": "02/05/2018 10:00:00",
            "status": "aguardando_agendamento",
            "atendimento": {"usuario_responsavel": "Guilherme Couto"},
        }
    ],
}

# Payload real de exemplo — rede/equipamento.rst, com uma interface "PON5"
# que casa com CLIENTE_RESPONSE (servicos[].interface.nome) — é a
# correlação que _resolve_olt_id() faz.
EQUIPAMENTO_RESPONSE = {
    "status": "success",
    "msg": "Dados consultados com sucesso.",
    "equipamentos": [
        {
            "id_equipamento": 1410,
            "nome": "OLT BDCOM",
            "ipv4": "177.52.48.7",
            "modelo": "GP3600-08",
            "fabricante": "BDCOM",
            "interfaces": [{"id_interface_conexao": 355, "nome": "PON5", "tipo": "gpon"}],
        },
        {
            "id_equipamento": 1406,
            "nome": "JUNIPER-MX104",
            "ipv4": "10.20.1.114",
            "modelo": "MX104",
            "fabricante": "JUNIPER",
            "interfaces": [],
        },
    ],
}

EQUIPAMENTO_RESPONSE_EMPTY = {"status": "success", "msg": "Dados consultados com sucesso.", "equipamentos": []}


def _configured_settings(**overrides) -> Settings:
    defaults = {
        "hubsoft_base_url": "https://provedor.hubsoft.com.br",
        "hubsoft_client_id": "3",
        "hubsoft_client_secret": "ONe7Ns48Y30tB",
        "hubsoft_username": "teste@teste.com",
        "hubsoft_password": "1234",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _auth_aware_handler(data_handler):
    """Simula o `/oauth/token` de verdade e delega o resto ao handler do teste."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json=AUTH_RESPONSE)
        return data_handler(request)

    return handler


def _connector_with(data_handler) -> HubsoftConnector:
    transport = httpx.MockTransport(_auth_aware_handler(data_handler))
    client = httpx.AsyncClient(transport=transport)
    return HubsoftConnector(_configured_settings(), client=client)


def _with_empty_equipamentos(data_handler):
    """Envolve um handler de teste para responder `/rede/equipamento` com
    uma lista vazia — usado em testes cujo foco não é resolução de OLT
    (find_subscriber sempre dispara essa chamada; sem branch explícito, o
    handler original quebraria o assert de outro endpoint)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE_EMPTY)
        return data_handler(request)

    return handler


def test_hubsoft_implements_protocol():
    assert isinstance(_connector_with(lambda r: pytest.fail("não deveria chamar a API")), ISPConnector)


@pytest.mark.parametrize(
    "missing_field",
    ["hubsoft_base_url", "hubsoft_client_id", "hubsoft_client_secret", "hubsoft_username", "hubsoft_password"],
)
def test_hubsoft_requires_all_credentials(missing_field):
    with pytest.raises(HubsoftNotConfiguredError):
        HubsoftConnector(_configured_settings(**{missing_field: ""}))


def test_factory_wires_hubsoft_connector():
    connector = get_connector("hubsoft", settings=_configured_settings())
    assert isinstance(connector, HubsoftConnector)


# -- find_subscriber -------------------------------------------------------


async def test_find_subscriber_parses_real_payload():
    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE_EMPTY)
        assert request.url.path == "/api/v1/integracao/cliente"
        assert request.url.params["busca"] == "cpf_cnpj"
        assert request.url.params["termo_busca"] == "10682083681"
        assert request.headers["authorization"] == "Bearer fake-access-token"
        return httpx.Response(200, json=CLIENTE_RESPONSE)

    connector = _connector_with(data_handler)
    subscriber = await connector.find_subscriber(cpf="106.820.836-81")

    assert subscriber is not None
    assert subscriber.id == "11201"  # id_cliente_servico, não id_cliente
    assert subscriber.name == "GUILHERME SILVA"
    assert subscriber.phone == "37988242968"
    assert subscriber.plan_name == "4M"
    assert subscriber.address == "RUA MINAS GERAIS, 1793 - IPIRANGA"
    assert subscriber.pon == "PON5"
    assert subscriber.contract_start.isoformat() == "2017-08-05"
    assert "***" in subscriber.cpf_masked  # nunca CPF em claro (LGPD §6.3)
    # Confirmado que não existem em /rede/equipamento, /rede/pop nem
    # /rede/zona_atendimento — ver docs/connectors/hubsoft.md.
    assert subscriber.cpe_serial is None
    assert subscriber.cto_id is None


async def test_find_subscriber_returns_none_when_empty():
    def data_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "clientes": []})

    connector = _connector_with(data_handler)
    assert await connector.find_subscriber(cpf="00000000000") is None


async def test_find_subscriber_without_cpf_or_phone_returns_none_without_calling_api():
    connector = _connector_with(lambda r: pytest.fail("não deveria chamar a API"))
    assert await connector.find_subscriber() is None


# -- resolução de OLT via rede/equipamento -----------------------------------


async def test_find_subscriber_resolves_olt_id_from_matching_interface():
    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE)
        return httpx.Response(200, json=CLIENTE_RESPONSE)

    connector = _connector_with(data_handler)
    subscriber = await connector.find_subscriber(cpf="10682083681")

    assert subscriber is not None
    assert subscriber.pon == "PON5"
    assert subscriber.olt_id == "1410"  # id_equipamento da OLT dona da interface PON5


async def test_find_subscriber_olt_id_none_when_no_interface_matches():
    connector = _connector_with(_with_empty_equipamentos(lambda r: httpx.Response(200, json=CLIENTE_RESPONSE)))
    subscriber = await connector.find_subscriber(cpf="10682083681")

    assert subscriber is not None
    assert subscriber.olt_id is None


async def test_find_subscriber_olt_resolution_degrades_gracefully_on_failure():
    """Se /rede/equipamento falhar, find_subscriber não quebra — a
    resolução de OLT é um enriquecimento best-effort (§4.4)."""

    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(500, text="erro interno simulado")
        return httpx.Response(200, json=CLIENTE_RESPONSE)

    connector = _connector_with(data_handler)
    subscriber = await connector.find_subscriber(cpf="10682083681")

    assert subscriber is not None
    assert subscriber.olt_id is None


async def test_equipamento_list_is_cached_across_find_subscriber_calls():
    call_count = {"equipamento": 0}

    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            call_count["equipamento"] += 1
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE)
        return httpx.Response(200, json=CLIENTE_RESPONSE)

    connector = _connector_with(data_handler)
    await connector.find_subscriber(cpf="10682083681")
    await connector.find_subscriber(cpf="10682083681")

    assert call_count["equipamento"] == 1


# -- get_invoices / issue_second_copy ---------------------------------------


async def test_get_invoices_filters_by_status_and_infers_it():
    def data_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/integracao/cliente/financeiro"
        assert request.url.params["busca"] == "id_cliente_servico"
        return httpx.Response(200, json=FATURAS_RESPONSE)

    connector = _connector_with(data_handler)

    open_invoices = await connector.get_invoices("11201", status="open")
    assert [i.id for i in open_invoices] == ["36403"]
    assert open_invoices[0].amount_cents == 1000
    assert open_invoices[0].barcode_digitable_line is not None

    paid_invoices = await connector.get_invoices("11201", status="paid")
    assert [i.id for i in paid_invoices] == ["36397"]
    assert paid_invoices[0].amount_cents == 5495


async def test_issue_second_copy_uses_cached_invoice_from_get_invoices():
    connector = _connector_with(lambda r: httpx.Response(200, json=FATURAS_RESPONSE))
    await connector.get_invoices("11201", status="all")

    payload = await connector.issue_second_copy("36403")

    assert payload.pix_copy_paste.startswith("000201")
    assert payload.digitable_line.startswith("75691")
    assert payload.amount_cents == 1000


async def test_issue_second_copy_without_prior_lookup_raises():
    connector = _connector_with(lambda r: pytest.fail("não deveria chamar a API"))
    with pytest.raises(ConnectorError, match="get_invoices"):
        await connector.issue_second_copy("99999")


async def test_issue_second_copy_raises_when_no_pix_or_linha_available():
    connector = _connector_with(lambda r: httpx.Response(200, json=FATURAS_RESPONSE))
    await connector.get_invoices("11201", status="all")  # cacheia as duas, inclusive a paga sem PIX/linha

    with pytest.raises(ConnectorError, match="sem PIX"):
        await connector.issue_second_copy("36397")


# -- request_trust_unlock ----------------------------------------------------


async def test_request_trust_unlock_success():
    def data_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/integracao/cliente/desbloqueio_confianca"
        body = request.read()
        assert b'"id_cliente_servico": "11201"' in body or b'"id_cliente_servico":"11201"' in body
        return httpx.Response(
            200,
            json={"status": "success", "msg": "Desbloqueio em confiança realizado com sucesso até 26/11/2018"},
        )

    connector = _connector_with(data_handler)
    result = await connector.request_trust_unlock("11201")

    assert result.eligible is True
    assert result.unlocked_until is not None


async def test_request_trust_unlock_ineligible():
    def data_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "msg": "valor excede configuração"})

    connector = _connector_with(data_handler)
    result = await connector.request_trust_unlock("11201")

    assert result.eligible is False
    assert result.reason == "valor excede configuração"


# -- get_connection_status ---------------------------------------------------


async def test_get_connection_status_requires_prior_find_subscriber():
    connector = _connector_with(lambda r: pytest.fail("não deveria chamar a API"))
    with pytest.raises(ConnectorError, match="find_subscriber"):
        await connector.get_connection_status("11201")


async def test_get_connection_status_online():
    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE_EMPTY)
        if request.url.path == "/api/v1/integracao/cliente":
            return httpx.Response(200, json=CLIENTE_RESPONSE)
        assert request.url.path == "/api/v1/integracao/cliente/extrato_conexao"
        assert request.url.params["busca"] == "login"
        assert request.url.params["termo_busca"] == "guilhermesilva1068"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "registros": [{"acctstarttime": "2017-09-27 18:14:08-03", "acctstoptime": None}],
            },
        )

    connector = _connector_with(data_handler)
    await connector.find_subscriber(cpf="10682083681")  # popula o cache de login

    status = await connector.get_connection_status("11201")

    assert status.session_state.value == "online"
    assert status.last_logon is not None


async def test_get_connection_status_offline():
    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE_EMPTY)
        if request.url.path == "/api/v1/integracao/cliente":
            return httpx.Response(200, json=CLIENTE_RESPONSE)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "registros": [
                    {"acctstarttime": "2017-09-27 18:14:08-03", "acctstoptime": "2017-09-27 20:00:00-03"}
                ],
            },
        )

    connector = _connector_with(data_handler)
    await connector.find_subscriber(cpf="10682083681")

    status = await connector.get_connection_status("11201")

    assert status.session_state.value == "offline"
    assert status.last_logoff is not None


# -- diagnóstico/reboot de CPE e incidentes: confirmado que não existem na Hubsoft --


async def test_get_cpe_diagnostics_not_available_in_hubsoft():
    connector = _connector_with(lambda r: pytest.fail("não deveria chamar a API"))
    with pytest.raises(NotImplementedError, match="ACS/NMS"):
        await connector.get_cpe_diagnostics("ONU-1")


async def test_reboot_cpe_not_available_in_hubsoft():
    connector = _connector_with(lambda r: pytest.fail("não deveria chamar a API"))
    with pytest.raises(NotImplementedError, match="ACS/NMS"):
        await connector.reboot_cpe("ONU-1", idempotency_key="idem-1")


async def test_get_area_incidents_not_available_in_hubsoft():
    connector = _connector_with(lambda r: pytest.fail("não deveria chamar a API"))
    with pytest.raises(NotImplementedError, match="ACS/NMS"):
        await connector.get_area_incidents("OLT-1", "PON-1")


# -- ordens de serviço --------------------------------------------------------


async def test_list_service_orders_maps_status_and_technician():
    connector = _connector_with(lambda r: httpx.Response(200, json=ORDENS_SERVICO_RESPONSE))

    orders = await connector.list_service_orders("11201")

    assert len(orders) == 1
    assert orders[0].id == "78"
    assert orders[0].status == ServiceOrderStatus.OPEN  # aguardando_agendamento -> OPEN
    assert orders[0].technician == "Guilherme Couto"  # via atendimento.usuario_responsavel


async def test_create_service_order_via_atendimento_endpoint():
    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE_EMPTY)
        if request.url.path == "/api/v1/integracao/cliente":
            return httpx.Response(200, json=CLIENTE_RESPONSE)
        assert request.url.path == "/api/v1/integracao/atendimento"
        body = _json.loads(request.read())
        assert body["abrir_os"] is True
        assert body["id_cliente_servico"] == "11201"
        assert body["nome"] == "GUILHERME SILVA"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "protocolo": "201811161058216",
                "ordens_servico": [{"id_ordem_servico": 1493, "numero": "1262", "status": "Pendente"}],
            },
        )

    connector = _connector_with(data_handler)
    await connector.find_subscriber(cpf="10682083681")  # popula contato p/ /atendimento

    so = await connector.create_service_order(
        SODraft(subscriber_id="11201", category="sem_sinal", summary="Cliente sem sinal")
    )

    assert so.id == "1493"
    assert so.category == "sem_sinal"


async def test_create_protocol_via_atendimento_endpoint():
    def data_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/integracao/rede/equipamento":
            return httpx.Response(200, json=EQUIPAMENTO_RESPONSE_EMPTY)
        if request.url.path == "/api/v1/integracao/cliente":
            return httpx.Response(200, json=CLIENTE_RESPONSE)
        body = _json.loads(request.read())
        assert body.get("abrir_os") is None  # create_protocol não abre OS
        return httpx.Response(200, json={"status": "success", "protocolo": "201811161058216"})

    connector = _connector_with(data_handler)
    await connector.find_subscriber(cpf="10682083681")

    protocol = await connector.create_protocol("11201", "Transbordo: teste")

    assert protocol.protocol_number == "201811161058216"


# -- Parsers/helpers puros (spec §4.4 — mapeamento de payload real) ---------


def test_mask_cpf_format():
    assert _mask_cpf("10682083681") == "106.***.**6-81"


def test_parse_br_date():
    assert _parse_br_date("10/10/2017").isoformat() == "2017-10-10"


def test_parse_radius_datetime_handles_two_digit_offset():
    """"2017-09-27 18:14:08-03" — offset sem minutos, que
    `datetime.fromisoformat` sozinho rejeita."""
    parsed = _parse_radius_datetime("2017-09-27 18:14:08-03")
    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == -3 * 3600


def test_parse_radius_datetime_none():
    assert _parse_radius_datetime(None) is None


def test_infer_invoice_status_paid_when_data_pagamento_set():
    assert _infer_invoice_status("31/07/2017", date(2020, 1, 1)) == InvoiceStatus.PAID


def test_infer_invoice_status_overdue_when_unpaid_and_past_due():
    assert _infer_invoice_status(None, date(2000, 1, 1)) == InvoiceStatus.OVERDUE


def test_infer_invoice_status_open_when_unpaid_and_future_due():
    assert _infer_invoice_status(None, date(2099, 1, 1)) == InvoiceStatus.OPEN


def test_map_so_status_known_and_unknown():
    assert _map_so_status("finalizado") == ServiceOrderStatus.DONE
    assert _map_so_status("algo-nunca-visto") == ServiceOrderStatus.OPEN
