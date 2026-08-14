from voxisp.connectors.base import ConnectorError, ISPConnector, SubscriberNotFoundError
from voxisp.connectors.hubsoft import HubsoftConnector, HubsoftNotConfiguredError
from voxisp.connectors.mock import MockISPConnector

__all__ = [
    "ConnectorError",
    "HubsoftConnector",
    "HubsoftNotConfiguredError",
    "ISPConnector",
    "MockISPConnector",
    "SubscriberNotFoundError",
    "get_connector",
]


def get_connector(name: str = "mock", settings=None) -> ISPConnector:
    """Fábrica de conectores. Novas implementações (IXC, SGP, Voalle, MK
    Solutions) se registram aqui conforme forem escritas — ver spec §4.4.

    `HubsoftConnector` está stubado (ver docs/connectors/hubsoft.md):
    instancia normalmente, mas cada método real levanta
    `NotImplementedError` até a documentação da API chegar.
    """
    if name == "mock":
        return MockISPConnector()
    if name == "hubsoft":
        if settings is None:
            from voxisp.config import settings as default_settings

            settings = default_settings
        return HubsoftConnector(settings)
    raise ValueError(
        f"Conector '{name}' ainda não implementado. Disponíveis: mock, hubsoft (stub). "
        "Implemente ISPConnector em voxisp/connectors/<nome>.py e registre em get_connector()."
    )
