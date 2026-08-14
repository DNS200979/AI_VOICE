"""HubsoftConnector é um stub aguardando a documentação da API — ver
docs/connectors/hubsoft.md. Estes testes garantem que:

1. Ele já satisfaz o contrato `ISPConnector` (assinatura correta).
2. Falha de forma clara e explícita quando não configurado.
3. Cada método real avisa que está pendente, em vez de falhar silenciosamente.

Conforme os métodos forem implementados de verdade, os testes correspondentes
saem daqui e viram testes de contrato reais (iguais aos de `MockISPConnector`).
"""
import pytest

from voxisp.config import Settings
from voxisp.connectors import get_connector
from voxisp.connectors.base import ISPConnector
from voxisp.connectors.hubsoft import HubsoftConnector, HubsoftNotConfiguredError


def _configured_settings() -> Settings:
    return Settings(
        hubsoft_base_url="https://hubsoft.example.com/api",
        hubsoft_client_id="dummy",
        hubsoft_client_secret="dummy",
    )


def test_hubsoft_implements_protocol():
    assert isinstance(HubsoftConnector(_configured_settings()), ISPConnector)


def test_hubsoft_requires_base_url():
    with pytest.raises(HubsoftNotConfiguredError):
        HubsoftConnector(Settings(hubsoft_base_url=""))


def test_factory_wires_hubsoft_connector():
    connector = get_connector("hubsoft", settings=_configured_settings())
    assert isinstance(connector, HubsoftConnector)


async def test_find_subscriber_not_implemented_yet():
    connector = HubsoftConnector(_configured_settings())
    with pytest.raises(NotImplementedError, match="find_subscriber"):
        await connector.find_subscriber(cpf="11144477735")


async def test_reboot_cpe_not_implemented_yet():
    connector = HubsoftConnector(_configured_settings())
    with pytest.raises(NotImplementedError, match="reboot_cpe"):
        await connector.reboot_cpe("ONU-X", idempotency_key="idem-1")
