from voxisp.connectors.base import ConnectorError, ISPConnector, SubscriberNotFoundError
from voxisp.connectors.genieacs import (
    GenieACSAdapter,
    GenieACSNotConfiguredError,
    get_genieacs_adapter,
)
from voxisp.connectors.hub import ConnectorHub
from voxisp.connectors.hubsoft import HubsoftConnector, HubsoftNotConfiguredError
from voxisp.connectors.mock import MockISPConnector
from voxisp.connectors.zabbix import ZabbixAdapter, ZabbixNotConfiguredError, get_zabbix_adapter

__all__ = [
    "ConnectorError",
    "ConnectorHub",
    "GenieACSAdapter",
    "GenieACSNotConfiguredError",
    "HubsoftConnector",
    "HubsoftNotConfiguredError",
    "ISPConnector",
    "MockISPConnector",
    "SubscriberNotFoundError",
    "ZabbixAdapter",
    "ZabbixNotConfiguredError",
    "get_connector",
    "get_genieacs_adapter",
    "get_zabbix_adapter",
]


def get_connector(name: str = "mock", settings=None) -> ISPConnector:
    """Fábrica de conectores. Novas implementações de ERP (IXC, SGP,
    Voalle, MK Solutions) se registram aqui conforme forem escritas — ver
    spec §4.4.

    Sempre devolve um `ConnectorHub` compondo o ERP escolhido com os
    adapters de ACS/NMS que estiverem configurados (`ACS_PROVIDER`/
    `NMS_PROVIDER` no `.env`) — sem eles, o Hub vira um passthrough
    transparente para o ERP (comportamento idêntico a usar o ERP puro).
    """
    if settings is None:
        from voxisp.config import settings as default_settings

        settings = default_settings

    if name == "mock":
        erp: ISPConnector = MockISPConnector()
    elif name == "hubsoft":
        erp = HubsoftConnector(settings)
    else:
        raise ValueError(
            f"Conector '{name}' ainda não implementado. Disponíveis: mock, hubsoft. "
            "Implemente ISPConnector em voxisp/connectors/<nome>.py e registre em get_connector()."
        )

    return ConnectorHub(erp, acs=get_genieacs_adapter(settings), nms=get_zabbix_adapter(settings))
