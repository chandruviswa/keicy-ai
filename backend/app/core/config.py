from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_env: str = "development"
    log_format: str = "json"  # json | text
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "keicy"
    db_user: str = "keicy"
    db_password: str = "keicy"
    # Optional full URL; when set it overrides the DB_* parts (used by tests).
    database_url_override: str = Field("", alias="DATABASE_URL")

    # --- AI gateway ---
    # Providers tried in order. Unconfigured cloud providers (no API key) are skipped.
    llm_providers: str = "anthropic,ollama"
    llm_timeout_s: float = 60
    llm_max_retries: int = 2  # attempts per provider for retryable errors (timeout, 429, 5xx)
    llm_retry_backoff_s: float = 1.0

    anthropic_api_key: str = ""
    anthropic_model: str = Field("claude-sonnet-5-5", validation_alias=AliasChoices("ANTHROPIC_MODEL", "CLOUD_LLM_MODEL"))

    openai_api_key: str = ""
    openai_model: str = "gpt-5"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    ollama_timeout_s: float = 180  # local models are slower than cloud
    ollama_think: bool = False  # reasoning models (qwen3.x, etc.) are far faster with thinking off

    paypal_mode: Literal["sandbox", "live"] = "sandbox"  # real PayPal only; there is no simulated mode
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_currency: str = "USD"
    paypal_webhook_id: str = ""  # from the app's webhook subscription in the PayPal developer dashboard
    paypal_timeout_s: float = 30

    approval_threshold: Decimal = Decimal("5000")  # above this, finance flags the PO for senior approval
    tax_rate: Decimal = Decimal("0")  # applied to PO subtotals, e.g. 0.0825 for 8.25%
    # --- Auth ---
    jwt_secret: str = ""  # required in production; a random per-process secret is used in development if empty
    access_token_minutes: int = 480
    # Show the seeded demo accounts on the login page (never in production).
    demo_accounts_enabled: bool = True

    @field_validator("llm_providers")
    @classmethod
    def _real_models_only(cls, v: str) -> str:
        names = {p.strip().lower() for p in v.split(",") if p.strip()}
        if not names:
            raise ValueError("LLM_PROVIDERS must name at least one provider (anthropic, openai, ollama)")
        if "mock" in names:
            raise ValueError("LLM_PROVIDERS=mock is no longer supported: use anthropic, openai or ollama")
        return v

    @property
    def llm_provider_list(self) -> list[str]:
        return [p.strip().lower() for p in self.llm_providers.split(",") if p.strip()]

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        user, pw = quote_plus(self.db_user), quote_plus(self.db_password)
        return f"postgresql+asyncpg://{user}:{pw}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def paypal_base_url(self) -> str:
        if self.paypal_mode == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
