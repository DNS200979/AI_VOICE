"""Detecção de incidente massivo — spec §4.5.

O bloco de maior ROI do produto: em dia de rompimento, todas as chamadas
de uma região têm a mesma causa raiz e a mesma resposta. Detectar isso
cedo evita abrir uma OS individual por chamada e permite responder com
previsão de normalização em vez de rodar o diagnóstico completo.
"""
from __future__ import annotations

from dataclasses import dataclass

from voxisp.connectors.base import ISPConnector
from voxisp.connectors.models import Incident, Subscriber

DEFAULT_LOS_THRESHOLD = 3


@dataclass(frozen=True)
class MassiveCheckResult:
    is_massive: bool
    incident: Incident | None = None

    @property
    def eta_message(self) -> str | None:
        if not self.is_massive or self.incident is None or self.incident.eta_resolution is None:
            return None
        return self.incident.eta_resolution.strftime("%Hh%M")


async def check_massive_incident(
    connector: ISPConnector,
    subscriber: Subscriber,
    *,
    los_threshold: int = DEFAULT_LOS_THRESHOLD,
) -> MassiveCheckResult:
    """Implementa o algoritmo da spec §4.5, passos 1-4.

    1. Identificar assinante -> já resolvido (parâmetro `subscriber`), com
       OLT/PON/CTO vindos do cadastro.
    2/3. Contar ONUs em LOS/Dying Gasp na mesma PON (via `get_area_incidents`,
         que no conector real correlaciona com alarme do NMS/Zabbix).
    4. Caso contrário, segue para diagnóstico individual (`is_massive=False`).
    """
    if not subscriber.olt_id or not subscriber.pon:
        return MassiveCheckResult(is_massive=False)

    incidents = await connector.get_area_incidents(subscriber.olt_id, subscriber.pon)
    if not incidents:
        return MassiveCheckResult(is_massive=False)

    # Pega o incidente mais relevante (maior nº de afetados) na PON do assinante.
    incident = max(incidents, key=lambda i: i.affected_count)
    if incident.affected_count >= los_threshold:
        return MassiveCheckResult(is_massive=True, incident=incident)

    return MassiveCheckResult(is_massive=False)
