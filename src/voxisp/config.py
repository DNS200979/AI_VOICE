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

    # --- Hubsoft (ver docs/connectors/hubsoft.md — aguardando documentação da API) ---
    hubsoft_base_url: str = ""
    hubsoft_client_id: str = ""
    hubsoft_client_secret: str = ""

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
