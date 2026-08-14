from voxisp.connectors.base import ConnectorError, ISPConnector, SubscriberNotFoundError
from voxisp.connectors.mock import MockISPConnector

__all__ = [
    "ConnectorError",
    "ISPConnector",
    "MockISPConnector",
    "SubscriberNotFoundError",
    "get_connector",
]


def get_connector(name: str = "mock") -> ISPConnector:
    """Fábrica de conectores. Novas implementações (Hubsoft, IXC, SGP,
    Voalle, MK Solutions) se registram aqui conforme forem escritas —
    ver spec §4.4."""
    if name == "mock":
        return MockISPConnector()
    raise ValueError(
        f"Conector '{name}' ainda não implementado. Disponíveis: mock. "
        "Implemente ISPConnector em voxisp/connectors/<nome>.py e registre em get_connector()."
    )
