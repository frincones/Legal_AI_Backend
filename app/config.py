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
    brave_search_endpoint: str = "https://api.search.brave.com/res/v1/web/search"
    firecrawl_api_key: str = ""
    firecrawl_api_base: str = "https://api.firecrawl.dev"
    context7_api_key: str = ""

    # Embeddings (modelo local fastembed)
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Extended thinking (0 = desactivado). budget < max_tokens.
    thinking_budget: int = 1536

    # verificar_fuente — motor de verificación/grounding legal (Colombia)
    vf_enabled: bool = True
    vf_ttl_vigente_days: int = 30        # una norma vigente puede cambiar → revalidar
    vf_ttl_derogada_days: int = 3650     # derogada/inexequible no "revive"
    vf_ttl_no_encontrada_days: int = 1   # reintentar pronto
    vf_max_fetch: int = 3                # tope de fetches por consulta
    vf_max_saltos: int = 2               # tope de saltos (índice→documento)
    vf_max_consultas: int = 8            # tope de citas por llamada (batch)

    # CORS — orígenes del frontend (coma-separados)
    cors_origins: str = "*"


settings = Settings()
