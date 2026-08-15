"""Conector Hubsoft — implementação real, contra a API pública documentada.

Endpoints e formatos de payload verificados em:
- https://docs.hubsoft.com.br/ (documentação oficial)
- https://github.com/hubsoftbrasil/api (fonte .rst da doc acima)
Ver `docs/connectors/hubsoft.md` para o checklist completo, incluindo o que
NÃO existe na API da Hubsoft (confirmado pela pesquisa, não suposição):
diagnóstico/reboot de CPE e correlação de incidente de rede vêm do ACS/NMS,
nunca do ERP — exatamente como a spec §4.4 já previa.

Autenticação: OAuth2 "password grant" (`POST /oauth/token`), token Bearer
válido por ~30 dias. Cada instância mantém seu próprio token em memória e
reautentica sozinha quando expira ou quando o servidor devolve 401.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

import httpx

from voxisp.config import Settings
from voxisp.connectors.base import ConnectorError, ISPConnector
from voxisp.connectors.models import (
    ActionResult,
    ConnectionStatus,
    CPEDiagnostics,
    Incident,
    Invoice,
    InvoiceStatus,
    PaymentPayload,
    ServiceOrder,
    ServiceOrderStatus,
    SessionState,
    SODraft,
    Subscriber,
    UnlockResult,
    VisitAction,
    VisitDraft,
)
from voxisp.connectors.models import (
    Protocol as ProtocolRecord,
)
from voxisp.connectors.resilience import CircuitBreaker, call_with_resilience

_NOT_AVAILABLE_IN_HUBSOFT = (
    "HubsoftConnector.{method}: confirmado por pesquisa na doc oficial "
    "(github.com/hubsoftbrasil/api) que este dado NÃO vem do ERP Hubsoft — "
    "precisa de um adapter de ACS/NMS separado (GenieACSAdapter, "
    "OLTSnmpAdapter etc., spec §4.4). Ver docs/connectors/hubsoft.md."
)

_SO_STATUS_MAP: dict[str, ServiceOrderStatus] = {
    "pendente": ServiceOrderStatus.OPEN,
    "aguardando_agendamento": ServiceOrderStatus.OPEN,
    "aguardando agendamento": ServiceOrderStatus.OPEN,
    "em_andamento": ServiceOrderStatus.IN_PROGRESS,
    "em andamento": ServiceOrderStatus.IN_PROGRESS,
    "finalizado": ServiceOrderStatus.DONE,
    "cancelado": ServiceOrderStatus.CANCELLED,
    "cancelada": ServiceOrderStatus.CANCELLED,
}

_TZ_OFFSET_NO_MINUTES_RE = re.compile(r"^(?P<base>.+\d{2}:\d{2}:\d{2})(?P<sign>[+-])(?P<hh>\d{2})$")


def _mask_cpf(cpf_cnpj: str) -> str:
    """Mesmo formato usado no restante do projeto (LGPD §6.3): nunca CPF em claro."""
    digits = re.sub(r"\D", "", cpf_cnpj or "")
    if len(digits) != 11:
        return "***.***.***-**"
    return f"{digits[0:3]}.***.**{digits[8]}-{digits[9:11]}"


def _parse_br_date(value: str) -> date:
    """Datas de fatura/OS chegam como "DD/MM/YYYY" (verificado em financeiro.rst)."""
    return datetime.strptime(value, "%d/%m/%Y").date()  # noqa: DTZ007 - só a data importa aqui


def _parse_br_datetime(value: str) -> datetime:
    """"DD/MM/YYYY HH:MM:SS", ex. `data_cadastro` de ordem_servico."""
    return datetime.strptime(value, "%d/%m/%Y %H:%M:%S").replace(tzinfo=UTC)


def _parse_iso_datetime(value: str) -> datetime:
    """"YYYY-MM-DD HH:MM:SS", ex. `data_cadastro` de cliente."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _parse_radius_datetime(value: str | None) -> datetime | None:
    """Timestamps do extrato_conexao vêm como "2017-09-27 18:14:08-03" — offset
    de fuso sem minutos, que `datetime.fromisoformat` não aceita direto."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        match = _TZ_OFFSET_NO_MINUTES_RE.match(value)
        if not match:
            raise
        return datetime.fromisoformat(f"{match['base']}{match['sign']}{match['hh']}:00")


def _infer_invoice_status(data_pagamento: str | None, due_date: date) -> InvoiceStatus:
    """A Hubsoft não devolve um campo de status explícito na fatura — infere
    a partir de `data_pagamento` (preenchida = paga) e do vencimento."""
    if data_pagamento:
        return InvoiceStatus.PAID
    if due_date < datetime.now(UTC).date():
        return InvoiceStatus.OVERDUE
    return InvoiceStatus.OPEN


def _map_so_status(raw_status: str) -> ServiceOrderStatus:
    return _SO_STATUS_MAP.get((raw_status or "").strip().lower(), ServiceOrderStatus.OPEN)


class HubsoftNotConfiguredError(Exception):
    """Alguma credencial obrigatória (`HUBSOFT_*`) está ausente do `.env`."""


class HubsoftConnector(ISPConnector):
    """Implementação real do `ISPConnector` para o ERP Hubsoft.

    Particularidades da API real que moldaram este código (todas verificadas,
    ver docstring do módulo):

    - Não existe endpoint de fatura avulsa por `id_fatura` — só busca por
      cliente. `issue_second_copy` por isso depende de `get_invoices` ter
      rodado antes na mesma instância (cache interno `_invoice_cache`).
    - `extrato_conexao` busca por `login` RADIUS, não pelo id do assinante —
      o login é capturado em `find_subscriber` e cacheado internamente.
    - Não há endpoint de "criar OS" isolado: `ordem_servico/agendar` exige
      uma OS **já existente**. A criação de fato acontece via
      `POST /atendimento` com `abrir_os=true`, que também é o endpoint que
      emite o protocolo (`create_protocol`) — os dois métodos convergem no
      mesmo endpoint real, variando o payload.
    - `manage_visit` (agendar/reagendar/cancelar visita, OPS-02) usa os 3
      endpoints reais e confirmados de `ordem_servico/`: `agendar` (só
      `id_ordem_servico`, sem janela — confirma o agendamento de uma OS que
      já tem `data_inicio_programado`), `reagendar` (exige janela nova de
      início/fim) e `remove_agendamento` (exige `id_motivo_remocao_agendamento`,
      sem catálogo fixo — ver `docs/connectors/hubsoft.md`). As três operam
      sobre uma OS já existente, nunca criam uma do zero.
    - Diagnóstico/reboot de CPE e correlação de incidente de rede
      (`get_cpe_diagnostics`, `reboot_cpe`, `get_area_incidents`) não
      existem na Hubsoft — vêm de ACS/NMS, fora do escopo deste conector.
    - `olt_id` é resolvido correlacionando `servicos[].interface.nome` (a
      PON do assinante, ex. "PON5") com `GET /rede/equipamento`: cada
      equipamento tem uma lista de `interfaces[]`, e o equipamento dono da
      interface com esse nome é a OLT. `cto_id`/`cpe_serial` continuam
      indisponíveis — confirmado que não aparecem em `/rede/equipamento`,
      `/rede/pop` nem `/rede/zona_atendimento` (ver docs/connectors/hubsoft.md).
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        missing = [
            name
            for name, value in (
                ("HUBSOFT_BASE_URL", settings.hubsoft_base_url),
                ("HUBSOFT_CLIENT_ID", settings.hubsoft_client_id),
                ("HUBSOFT_CLIENT_SECRET", settings.hubsoft_client_secret),
                ("HUBSOFT_USERNAME", settings.hubsoft_username),
                ("HUBSOFT_PASSWORD", settings.hubsoft_password),
            )
            if not value
        ]
        if missing:
            raise HubsoftNotConfiguredError(
                f"Faltam no .env: {', '.join(missing)}. Ver docs/connectors/hubsoft.md."
            )
        self._settings = settings
        self._base_url = settings.hubsoft_base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._breaker = CircuitBreaker()
        # Breaker isolado para a resolução de OLT (rede/equipamento): é um
        # enriquecimento best-effort, não pode derrubar o breaker principal
        # e travar chamadas críticas (financeiro, sessão) — spec §4.4,
        # "degradação graciosa".
        self._equipment_breaker = CircuitBreaker()
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        # Caches internos (por instância) para contornar lacunas da API real
        # — ver docstring da classe.
        self._subscriber_cache: dict[str, Subscriber] = {}
        self._login_by_subscriber: dict[str, str] = {}
        self._invoice_cache: dict[str, dict] = {}
        self._equipamento_cache: list[dict] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- Autenticação (OAuth2 password grant) -------------------------------

    async def _ensure_token(self) -> None:
        if (
            self._access_token is not None
            and self._token_expires_at is not None
            and datetime.now(UTC) < self._token_expires_at
        ):
            return
        await self._authenticate()

    async def _authenticate(self) -> None:
        async def _do_call() -> httpx.Response:
            return await self._client.post(
                f"{self._base_url}/oauth/token",
                json={
                    "grant_type": "password",
                    "client_id": self._settings.hubsoft_client_id,
                    "client_secret": self._settings.hubsoft_client_secret,
                    "username": self._settings.hubsoft_username,
                    "password": self._settings.hubsoft_password,
                },
                headers={"Accept": "application/json"},
            )

        response = await call_with_resilience(_do_call, breaker=self._breaker, max_retries=1)
        if response.status_code >= 400:
            raise ConnectorError(
                f"Hubsoft OAuth2 falhou: HTTP {response.status_code} — {response.text[:300]}"
            )
        data = response.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 300))
        # Margem de segurança de 60s para nunca usar um token na borda da expiração.
        self._token_expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in - 60, 30))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        max_retries: int = 0,
        breaker: CircuitBreaker | None = None,
    ) -> dict:
        """`max_retries=0` por padrão: chamadas que mutam estado (POST) não
        devem ser repetidas automaticamente sem idempotência garantida pela
        API real (spec §5) — quem faz GET explicitamente pede mais retries.
        `breaker` permite isolar chamadas não-críticas (ex.: resolução de
        OLT) do circuit breaker principal."""
        await self._ensure_token()
        breaker = breaker or self._breaker

        async def _do_call() -> httpx.Response:
            headers = {"Accept": "application/json", "Authorization": f"Bearer {self._access_token}"}
            return await self._client.request(
                method, f"{self._base_url}{path}", params=params, json=json_body, headers=headers
            )

        response = await call_with_resilience(_do_call, breaker=breaker, max_retries=max_retries)

        if response.status_code == 401:
            # Token pode ter expirado antes do previsto — reautentica uma vez.
            self._access_token = None
            await self._ensure_token()
            headers = {"Accept": "application/json", "Authorization": f"Bearer {self._access_token}"}
            response = await self._client.request(
                method, f"{self._base_url}{path}", params=params, json=json_body, headers=headers
            )

        if response.status_code >= 400:
            raise ConnectorError(
                f"Hubsoft {method} {path}: HTTP {response.status_code} — {response.text[:300]}"
            )
        return response.json()

    # -- ISPConnector ---------------------------------------------------------

    async def find_subscriber(
        self, cpf: str | None = None, phone: str | None = None
    ) -> Subscriber | None:
        if cpf:
            busca, termo = "cpf_cnpj", re.sub(r"\D", "", cpf)
        elif phone:
            busca, termo = "telefone", re.sub(r"\D", "", phone)
        else:
            return None

        data = await self._request(
            "GET",
            "/api/v1/integracao/cliente",
            params={"busca": busca, "termo_busca": termo, "limit": 1, "ultima_conexao": "sim"},
            max_retries=2,
        )
        clientes = data.get("clientes") or []
        if not clientes:
            return None

        raw = clientes[0]
        subscriber = self._parse_subscriber(raw)
        if subscriber.pon:
            subscriber.olt_id = await self._resolve_olt_id(subscriber.pon)
        self._subscriber_cache[subscriber.id] = subscriber
        servicos = raw.get("servicos") or []
        login = servicos[0].get("login") if servicos else None
        if login:
            self._login_by_subscriber[subscriber.id] = login
        return subscriber

    def _parse_subscriber(self, raw: dict) -> Subscriber:
        servicos = raw.get("servicos") or []
        servico = servicos[0] if servicos else {}
        endereco = servico.get("endereco_instalacao") or {}
        interface = servico.get("interface") or {}
        subscriber_id = servico.get("id_cliente_servico") or raw.get("id_cliente")
        if subscriber_id is None:
            raise ConnectorError("cliente Hubsoft sem id_cliente_servico/id_cliente na resposta")

        return Subscriber(
            id=str(subscriber_id),
            name=(raw.get("nome_razaosocial") or "Cliente Hubsoft").strip(),
            cpf_masked=_mask_cpf(raw.get("cpf_cnpj", "")),
            phone=raw.get("telefone_primario") or raw.get("telefone_secundario") or "",
            plan_name=servico.get("nome", ""),
            contract_start=(
                _parse_iso_datetime(raw["data_cadastro"]).date()
                if raw.get("data_cadastro")
                else datetime.now(UTC).date()
            ),
            # TODO(hubsoft-docs): fidelidade não aparece em /cliente — checar
            # se há campo em outro endpoint (contrato?) quando formos usar FIN-04.
            loyalty_until=None,
            address=endereco.get("completo", ""),
            # olt_id é preenchido de forma assíncrona em find_subscriber()
            # (_resolve_olt_id), depois deste parse síncrono — não dá pra
            # resolver aqui dentro porque exige uma chamada HTTP adicional.
            olt_id=None,
            pon=interface.get("nome"),
            # CONFIRMADO (não suposição): cto_id não aparece em
            # /rede/equipamento, /rede/pop nem /rede/zona_atendimento — a
            # Hubsoft não expõe granularidade de CTO/caixa de emenda nesses
            # três endpoints. Fica pendente até acharmos outro endpoint ou
            # aceitarmos que vem só do NMS.
            cto_id=None,
            # CPE/ONU fica no ACS, não no ERP — spec §4.4, confirmado pela
            # pesquisa (nenhum dos 3 endpoints de rede/ tem serial de ONU).
            cpe_serial=None,
        )

    async def _resolve_olt_id(self, pon_interface_name: str) -> str | None:
        """Correlaciona a PON do assinante (`servicos[].interface.nome`,
        ex. "PON5") com `GET /rede/equipamento`: o equipamento que tem uma
        interface com esse nome é a OLT. Best-effort — nunca derruba
        `find_subscriber` se a API de rede falhar (§4.4, degradação graciosa)."""
        try:
            equipamentos = await self._get_equipamentos()
        except Exception:  # noqa: BLE001 - enriquecimento opcional, nunca propaga (§4.4)
            return None
        for equipamento in equipamentos:
            for interface in equipamento.get("interfaces") or []:
                if interface.get("nome") == pon_interface_name:
                    return str(equipamento.get("id_equipamento"))
        return None

    async def _get_equipamentos(self) -> list[dict]:
        if self._equipamento_cache is None:
            data = await self._request(
                "GET",
                "/api/v1/integracao/rede/equipamento",
                max_retries=1,
                breaker=self._equipment_breaker,
            )
            self._equipamento_cache = data.get("equipamentos") or []
        return self._equipamento_cache

    async def get_invoices(self, subscriber_id: str, status: str) -> list[Invoice]:
        data = await self._request(
            "GET",
            "/api/v1/integracao/cliente/financeiro",
            params={
                "busca": "id_cliente_servico",
                "termo_busca": subscriber_id,
                "limit": 50,
                "apenas_pendente": "nao",
            },
            max_retries=2,
        )
        invoices: list[Invoice] = []
        for raw in data.get("faturas") or []:
            invoice = self._parse_invoice(raw, subscriber_id)
            self._invoice_cache[invoice.id] = raw
            if status == "all" or invoice.status.value == status:
                invoices.append(invoice)
        return invoices

    def _parse_invoice(self, raw: dict, subscriber_id: str) -> Invoice:
        due = _parse_br_date(raw["data_vencimento"])
        return Invoice(
            id=str(raw["id_fatura"]),
            subscriber_id=subscriber_id,
            amount_cents=round(float(raw.get("valor", 0)) * 100),
            due_date=due,
            status=_infer_invoice_status(raw.get("data_pagamento"), due),
            barcode_digitable_line=raw.get("linha_digitavel"),
        )

    async def issue_second_copy(self, invoice_id: str) -> PaymentPayload:
        raw = self._invoice_cache.get(invoice_id)
        if raw is None:
            # A Hubsoft não tem endpoint de fatura avulsa por id_fatura —
            # só dá pra resolver isso se get_invoices já rodou antes nesta
            # instância (ver docstring da classe).
            raise ConnectorError(
                f"fatura {invoice_id} não está em cache — chame get_invoices() "
                "antes de issue_second_copy() (limitação real da API Hubsoft)"
            )
        pix = raw.get("pix_copia_cola") or ""
        linha = raw.get("linha_digitavel") or ""
        if not pix and not linha:
            raise ConnectorError(f"fatura {invoice_id} sem PIX nem linha digitável na Hubsoft")
        return PaymentPayload(
            invoice_id=invoice_id,
            pix_copy_paste=pix,
            digitable_line=linha,
            amount_cents=round(float(raw.get("valor", 0)) * 100),
            due_date=_parse_br_date(raw["data_vencimento"]),
        )

    async def request_trust_unlock(self, subscriber_id: str) -> UnlockResult:
        dias_desbloqueio = 2  # spec FIN-03: desbloqueio de 48h
        data = await self._request(
            "POST",
            "/api/v1/integracao/cliente/desbloqueio_confianca",
            json_body={"id_cliente_servico": subscriber_id, "dias_desbloqueio": dias_desbloqueio},
        )
        if data.get("status") != "success":
            return UnlockResult(
                subscriber_id=subscriber_id, eligible=False, reason=data.get("msg", "não elegível")
            )
        return UnlockResult(
            subscriber_id=subscriber_id,
            eligible=True,
            unlocked_until=datetime.now(UTC) + timedelta(days=dias_desbloqueio),
        )

    async def get_connection_status(self, subscriber_id: str) -> ConnectionStatus:
        login = self._login_by_subscriber.get(subscriber_id)
        if not login:
            raise ConnectorError(
                f"login RADIUS do assinante {subscriber_id} desconhecido — "
                "chame find_subscriber() antes (extrato_conexao busca por login, não por id)"
            )
        data = await self._request(
            "GET",
            "/api/v1/integracao/cliente/extrato_conexao",
            params={"busca": "login", "termo_busca": login, "limit": 1},
            max_retries=2,
        )
        registros = data.get("registros") or []
        if not registros:
            return ConnectionStatus(subscriber_id=subscriber_id, session_state=SessionState.UNKNOWN)

        record = registros[0]
        last_logon = _parse_radius_datetime(record.get("acctstarttime"))
        if record.get("acctstoptime") is None:
            return ConnectionStatus(
                subscriber_id=subscriber_id, session_state=SessionState.ONLINE, last_logon=last_logon
            )
        return ConnectionStatus(
            subscriber_id=subscriber_id,
            session_state=SessionState.OFFLINE,
            last_logon=last_logon,
            last_logoff=_parse_radius_datetime(record.get("acctstoptime")),
            # TODO(hubsoft-docs): extrato_conexao não expõe motivo de
            # desconexão — se existir em outro endpoint, mapear aqui.
            disconnect_reason=None,
        )

    async def get_cpe_diagnostics(self, cpe_serial: str) -> CPEDiagnostics:
        raise NotImplementedError(_NOT_AVAILABLE_IN_HUBSOFT.format(method="get_cpe_diagnostics"))

    async def reboot_cpe(self, cpe_serial: str, idempotency_key: str) -> ActionResult:
        raise NotImplementedError(_NOT_AVAILABLE_IN_HUBSOFT.format(method="reboot_cpe"))

    async def list_service_orders(self, subscriber_id: str) -> list[ServiceOrder]:
        data = await self._request(
            "GET",
            "/api/v1/integracao/cliente/ordem_servico",
            params={"busca": "id_cliente_servico", "termo_busca": subscriber_id, "limit": 20},
            max_retries=2,
        )
        return [self._parse_service_order(raw, subscriber_id) for raw in data.get("ordens_servico") or []]

    def _parse_service_order(self, raw: dict, subscriber_id: str) -> ServiceOrder:
        technician = None
        tecnicos = raw.get("tecnicos") or []
        if tecnicos:
            technician = tecnicos[0].get("name")
        else:
            atendimento = raw.get("atendimento") or {}
            technician = atendimento.get("usuario_responsavel")
        created_at = (
            _parse_br_datetime(raw["data_cadastro"]) if raw.get("data_cadastro") else datetime.now(UTC)
        )
        return ServiceOrder(
            id=str(raw["id_ordem_servico"]),
            subscriber_id=subscriber_id,
            category=raw.get("tipo", ""),
            status=_map_so_status(raw.get("status", "")),
            scheduled_window=raw.get("data_inicio_programado"),
            technician=technician,
            created_at=created_at,
        )

    async def create_service_order(self, payload: SODraft) -> ServiceOrder:
        # Não existe endpoint de "criar OS" isolado — a criação acontece via
        # /atendimento com abrir_os=true (ver docstring da classe).
        nome, telefone = self._contact_for(payload.subscriber_id)
        data = await self._request(
            "POST",
            "/api/v1/integracao/atendimento",
            json_body={
                "id_cliente_servico": payload.subscriber_id,
                "descricao": payload.summary,
                "nome": nome,
                "telefone": telefone,
                "abrir_os": True,
            },
        )
        ordens = data.get("ordens_servico") or []
        if not ordens:
            raise ConnectorError(f"Hubsoft não retornou ordem de serviço criada: {data.get('msg')}")
        raw_so = ordens[0]
        so_id = raw_so.get("id_ordem_servico", raw_so.get("id"))
        return ServiceOrder(
            id=str(so_id),
            subscriber_id=payload.subscriber_id,
            category=payload.category,
            status=ServiceOrderStatus.OPEN,
            scheduled_window=payload.preferred_window,
            technician=None,
            created_at=datetime.now(UTC),
        )

    async def manage_visit(self, draft: VisitDraft) -> ServiceOrder:
        """Agenda/reagenda/cancela uma visita técnica (OPS-02) contra os 3
        endpoints reais e confirmados `ordem_servico/agendar`,
        `/reagendar` e `/remove_agendamento`. Nenhum dos três cria uma OS
        — todos exigem `draft.service_order_id` de uma OS já existente
        (confirmado pela doc, ver docstring da classe)."""
        if draft.action == VisitAction.SCHEDULE:
            data = await self._request(
                "POST",
                "/api/v1/integracao/ordem_servico/agendar",
                json_body={"id_ordem_servico": draft.service_order_id},
            )
        elif draft.action == VisitAction.RESCHEDULE:
            if draft.window_start is None or draft.window_end is None:
                raise ConnectorError(
                    "reagendamento exige window_start e window_end — a Hubsoft "
                    "(ordem_servico/reagendar) exige data/hora de início e término"
                )
            data = await self._request(
                "POST",
                "/api/v1/integracao/ordem_servico/reagendar",
                json_body={
                    "id_ordem_servico": draft.service_order_id,
                    "data_inicio_programado": draft.window_start.strftime("%Y-%m-%d"),
                    "hora_inicio_programado": draft.window_start.strftime("%H:%M:%S"),
                    "data_termino_programado": draft.window_end.strftime("%Y-%m-%d"),
                    "hora_termino_programado": draft.window_end.strftime("%H:%M:%S"),
                },
            )
        elif draft.action == VisitAction.CANCEL:
            if not draft.reason or len(draft.reason.strip()) < 10:
                raise ConnectorError(
                    "cancelamento exige reason com pelo menos 10 caracteres — a "
                    "Hubsoft (ordem_servico/remove_agendamento) exige uma "
                    "'observacao' com esse mínimo"
                )
            if not self._settings.hubsoft_cancel_reason_id:
                raise ConnectorError(
                    "HUBSOFT_CANCEL_REASON_ID não configurado no .env — a Hubsoft "
                    "exige id_motivo_remocao_agendamento e NÃO tem um catálogo fixo "
                    "documentado (só disponível em GET /ordem_servico/create do "
                    "provedor). Ver docs/connectors/hubsoft.md."
                )
            data = await self._request(
                "POST",
                "/api/v1/integracao/ordem_servico/remove_agendamento",
                json_body={
                    "id_ordem_servico": draft.service_order_id,
                    "id_motivo_remocao_agendamento": self._settings.hubsoft_cancel_reason_id,
                    "observacao": draft.reason,
                },
            )
        else:
            raise ConnectorError(f"ação de visita desconhecida: {draft.action}")

        raw_so = data.get("ordem_servico")
        if raw_so is None:
            raise ConnectorError(f"Hubsoft não retornou ordem_servico em manage_visit: {data.get('msg')}")
        return self._parse_service_order(raw_so, draft.subscriber_id)

    async def get_area_incidents(self, olt_id: str, pon: str) -> list[Incident]:
        raise NotImplementedError(_NOT_AVAILABLE_IN_HUBSOFT.format(method="get_area_incidents"))

    async def create_protocol(self, subscriber_id: str, summary: str) -> ProtocolRecord:
        # Mesmo endpoint de create_service_order, sem abrir_os — ver docstring.
        nome, telefone = self._contact_for(subscriber_id)
        data = await self._request(
            "POST",
            "/api/v1/integracao/atendimento",
            json_body={
                "id_cliente_servico": subscriber_id,
                "descricao": summary,
                "nome": nome,
                "telefone": telefone,
            },
        )
        protocolo = data.get("protocolo")
        if not protocolo:
            raise ConnectorError(f"Hubsoft não retornou protocolo: {data.get('msg')}")
        return ProtocolRecord(
            protocol_number=str(protocolo),
            subscriber_id=subscriber_id,
            summary=summary,
            created_at=datetime.now(UTC),
        )

    def _contact_for(self, subscriber_id: str) -> tuple[str, str]:
        """`/atendimento` exige nome+telefone do solicitante — nem sempre
        disponíveis no escopo de quem chama create_protocol/create_service_order,
        então puxamos do cache populado por find_subscriber (mesma chamada
        que sempre precede estas nesta v1)."""
        subscriber = self._subscriber_cache.get(subscriber_id)
        if subscriber is None:
            return "Cliente", "00000000000"
        return subscriber.name, re.sub(r"\D", "", subscriber.phone) or "00000000000"
