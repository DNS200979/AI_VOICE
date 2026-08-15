"""Configuração central via variáveis de ambiente (ver .env.example)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://voxisp:voxisp@localhost:5432/voxisp"
    redis_url: str = "redis://localhost:6379/0"
    default_tenant_id: str = "demo"

    # Desligado por padrão: a API de demonstração roda sem nenhuma infra
    # (nem Postgres) até você optar por persistência real explicitamente.
    persistence_enabled: bool = False

    isp_connector: str = "mock"

    # --- Hubsoft (ver docs/connectors/hubsoft.md) ---
    # OAuth2 "password grant" — a API da Hubsoft exige as 4 credenciais
    # (client_id/client_secret identificam a integração; username/password
    # são de um usuário do sistema com permissão de API).
    hubsoft_base_url: str = ""
    hubsoft_client_id: str = ""
    hubsoft_client_secret: str = ""
    hubsoft_username: str = ""
    hubsoft_password: str = ""
    # `id_motivo_remocao_agendamento` exigido por `ordem_servico/remove_agendamento`
    # (cancelamento de visita, OPS-02). A Hubsoft não documenta um catálogo
    # fixo desses IDs — só aparecem em GET /ordem_servico/create do
    # provedor. 0 (padrão) = manage_visit(action=cancel) recusa a chamada
    # com erro explícito em vez de mandar um motivo inventado.
    hubsoft_cancel_reason_id: int = 0

    # --- ACS (ver docs/connectors/genieacs.md) — fonte de get_cpe_diagnostics/
    # reboot_cpe (spec §4.4). "none" (padrão) = ISPConnector.get_cpe_diagnostics/
    # reboot_cpe caem no que o ERP escolhido já faz (Mock funciona; Hubsoft
    # levanta NotImplementedError, confirmado que não existe no ERP).
    acs_provider: str = "none"  # none | genieacs
    genieacs_base_url: str = ""
    genieacs_username: str = ""
    genieacs_password: str = ""
    # Sem padrão TR-181 universal de potência óptica (confirmado — varia por
    # fabricante de ONT). Limiar de LOS em dBm: abaixo disso, ONUStatus.LOS.
    genieacs_rx_power_los_threshold_dbm: float = -28.0

    # --- NMS (ver docs/connectors/zabbix.md) — fonte de get_area_incidents
    # (spec §4.4/§4.5). "none" (padrão) = mesmo fallback do ACS acima.
    nms_provider: str = "none"  # none | zabbix
    zabbix_base_url: str = ""
    zabbix_username: str = ""
    zabbix_password: str = ""
    # Tag do host no Zabbix usada para correlacionar com o olt_id do ERP —
    # ver docs/connectors/zabbix.md para o porquê dessa convenção.
    zabbix_olt_tag_key: str = "olt_id"

    asr_provider: str = "stub"
    asr_api_key: str = ""

    tts_provider: str = "stub"
    tts_api_key: str = ""

    llm_provider: str = "stub"  # stub | anthropic
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5"

    ari_url: str = "http://localhost:8088/ari"
    ari_user: str = "voxisp"
    ari_password: str = "changeme"

    massive_los_threshold: int = 3
    massive_window_minutes: int = 15

    log_level: str = "INFO"


settings = Settings()
