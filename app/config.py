"""Configuración por variables de entorno (ver .env.example)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic / tiers
    anthropic_api_key: str = ""
    model_router: str = "claude-haiku-4-5-20251001"
    model_worker: str = "claude-sonnet-4-6"
    model_deep: str = "claude-opus-4-8"

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""          # Settings → API → JWT Secret (HS256)

    # Tools (Fase 2)
    e2b_api_key: str = ""
    brave_search_api_key: str = ""
    firecrawl_api_key: str = ""
    firecrawl_api_base: str = "https://api.firecrawl.dev"
    context7_api_key: str = ""

    # CORS — orígenes del frontend (coma-separados)
    cors_origins: str = "*"


settings = Settings()
