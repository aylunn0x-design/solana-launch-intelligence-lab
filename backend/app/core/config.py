"""Application configuration.

All configuration comes from environment variables (or a .env file). Nothing
secret is hardcoded. See .env.example for the full list.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Application ---
    app_name: str = "Solana Launch Intelligence Lab"
    environment: str = Field(
        default="development",
        description="development | test | production. Controls docs exposure and CORS.",
    )
    log_level: str = "INFO"

    # --- Database ---
    # SQLite by default so the project runs with zero infrastructure.
    # Set DATABASE_URL to a postgresql+psycopg:// URL for Postgres.
    database_url: str = Field(
        default="sqlite:///./solana_lab.db",
        description="SQLAlchemy URL. sqlite:///./file.db or postgresql+psycopg://user:pw@host/db",
    )

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Accept the `postgres://` URLs that managed hosts hand out.

        Render, Heroku, Railway and Supabase all expose a connection string
        beginning `postgres://`. SQLAlchemy 2 removed support for that alias and
        raises `Can't load plugin: sqlalchemy.dialects:postgres`, so a deploy
        that simply wires the host's connection string into DATABASE_URL fails
        at startup. Rewriting it here means the documented one-click deploy
        works without the operator having to know this.

        A URL that already names a driver (postgresql+psycopg://) is untouched.
        """
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://"):]
        return value

    # --- CORS: which frontend origins may call this API ---
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated allowed origins.",
    )

    # --- Provider rate limits (requests per minute) ---
    # These are deliberately conservative. The documented limits for the free
    # tiers could not be verified from the build environment (see DATA_SOURCES.md),
    # and a 429 was observed after a small burst against GeckoTerminal. The
    # limiter also backs off adaptively on 429, so these are a floor, not a
    # correctness dependency.
    geckoterminal_rate_limit_per_min: int = 25
    dexscreener_rate_limit_per_min: int = 50
    solana_rpc_rate_limit_per_min: int = 20

    http_timeout_seconds: float = 25.0
    http_max_retries: int = 4

    # --- Holder concentration ---
    # Disabled by default: the public Solana RPC returned HTTP 429 for
    # getTokenLargestAccounts on the first call, so holder data cannot be
    # collected reliably without a dedicated RPC provider. See DATA_SOURCES.md.
    enable_holder_collection: bool = False
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"

    # --- Research defaults ---
    # Hours of no recorded volume after which a pool is treated as inactive
    # ("death") for survival analysis. Parameterised because RQ6 asks how
    # sensitive conclusions are to this choice.
    default_inactivity_threshold_hours: int = 24
    # Minimum observations required before a pool enters an analytical dataset.
    min_candles_for_analysis: int = 6

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
