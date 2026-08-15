"""Smoke test manual do GenieACSAdapter/ZabbixAdapter contra software real.

Diferente da suíte em `tests/` (sempre contra `httpx.MockTransport`, nunca
rede), este script faz chamadas HTTP de verdade contra a stack local de
`docker-compose.test-infra.yml` — ver docs/testing/local-integration-stack.md
para o passo a passo completo de como subir essa stack antes de rodar isto.

Uso (com a stack no ar e o `.env` configurado):

    .venv/bin/python scripts/smoke_test_acs_nms.py acs <cpe_serial>
    .venv/bin/python scripts/smoke_test_acs_nms.py nms <olt_id> <pon>
    .venv/bin/python scripts/smoke_test_acs_nms.py acs-reboot <cpe_serial>

Não faz asserções — só imprime o que o adapter real devolveu, para
inspeção humana. Não substitui os testes automatizados; existe para
validar contra software real antes de existir um ISP piloto.
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from voxisp.config import settings
from voxisp.connectors.genieacs import get_genieacs_adapter
from voxisp.connectors.zabbix import get_zabbix_adapter


async def _run_acs(cpe_serial: str) -> None:
    adapter = get_genieacs_adapter(settings)
    if adapter is None:
        print("ACS_PROVIDER != genieacs no .env — nada a testar. Ver docs/testing/local-integration-stack.md.")
        return
    print(f"GET diagnóstico de {cpe_serial} em {settings.genieacs_base_url} ...")
    diag = await adapter.get_cpe_diagnostics(cpe_serial)
    print(diag.model_dump_json(indent=2))


async def _run_acs_reboot(cpe_serial: str) -> None:
    adapter = get_genieacs_adapter(settings)
    if adapter is None:
        print("ACS_PROVIDER != genieacs no .env — nada a testar.")
        return
    idem_key = f"smoke-test:{uuid.uuid4().hex[:8]}"
    print(f"POST reboot de {cpe_serial} em {settings.genieacs_base_url} (idempotency_key={idem_key}) ...")
    result = await adapter.reboot_cpe(cpe_serial, idempotency_key=idem_key)
    print(result.model_dump_json(indent=2))


async def _run_nms(olt_id: str, pon: str) -> None:
    adapter = get_zabbix_adapter(settings)
    if adapter is None:
        print("NMS_PROVIDER != zabbix no .env — nada a testar. Ver docs/testing/local-integration-stack.md.")
        return
    print(f"GET incidentes de olt_id={olt_id} em {settings.zabbix_base_url} ...")
    incidents = await adapter.get_area_incidents(olt_id, pon)
    if not incidents:
        print("Nenhum incidente ativo encontrado (host não achado, ou sem problem.get ativo).")
        return
    for incident in incidents:
        print(incident.model_dump_json(indent=2))


def _usage() -> None:
    print(__doc__)
    sys.exit(1)


async def _main() -> None:
    if len(sys.argv) < 2:
        _usage()
    command = sys.argv[1]
    if command == "acs" and len(sys.argv) == 3:
        await _run_acs(sys.argv[2])
    elif command == "acs-reboot" and len(sys.argv) == 3:
        await _run_acs_reboot(sys.argv[2])
    elif command == "nms" and len(sys.argv) == 4:
        await _run_nms(sys.argv[2], sys.argv[3])
    else:
        _usage()


if __name__ == "__main__":
    asyncio.run(_main())
