from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── App identity ──────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_NAME: str = "Kachu"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ── Security ──────────────────────────────────────────────────────────────
    SECRET_KEY: str = ""
    TOKEN_ENCRYPTION_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Database ──────────────────────────────────────────────────────────────
    # Direct URL used by v2 (supports SQLite for tests, PostgreSQL for production)
    DATABASE_URL: str = "postgresql+psycopg://kachu:kachu@localhost:5432/kachu"
    # Individual parts (v1 convention; used when DATABASE_URL is not set)
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "kachu"
    POSTGRES_USER: str = "kachu"
    POSTGRES_PASSWORD: str = ""

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── LiteLLM proxy (v1 uses a dedicated LiteLLM proxy) ────────────────────
    LITELLM_BASE_URL: str = "http://localhost:4000"
    LITELLM_MASTER_KEY: str = ""
    LITELLM_TENANT_BUDGET_DURATION: str = "monthly"
    LITELLM_MODEL: str = "gemini/gemini-3-flash-preview"  # direct model fallback

    # ── AI API keys ───────────────────────────────────────────────────────────
    GOOGLE_AI_API_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_AI_API_KEY", "GEMINI_API_KEY"),
    )
    OPENAI_API_KEY: str = ""
    COHERE_API_KEY: str = ""
    JINA_API_KEY: str = ""
    LLAMAPARSE_API_KEY: str = ""

    # ── Embeddings (v1 uses OpenAI text-embedding-3-small, dim 1536) ─────────
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSION: int = 1536

    # ── Qdrant ────────────────────────────────────────────────────────────────
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "kachu_knowledge"

    # ── Observability ─────────────────────────────────────────────────────────
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    OTEL_ENDPOINT: str = ""
    OTEL_SERVICE_NAME: str = "kachu"

    # ── LINE ──────────────────────────────────────────────────────────────────
    LINE_CHANNEL_ACCESS_TOKEN: str = ""
    LINE_CHANNEL_SECRET: str = ""
    LINE_CHANNEL_ID: str = ""
    LINE_LOGIN_CHANNEL_ID: str = ""
    LINE_LOGIN_CHANNEL_SECRET: str = ""
    LINE_REDIRECT_URI: str = ""
    LINE_BOSS_USER_ID: str = ""   # v2-specific single-tenant boss LINE user ID
    OAUTH_STATE_STORE_BACKEND: str = "auto"  # auto | redis | memory
    OAUTH_STATE_TTL_SECONDS: int = 600

    # ── Google OAuth ──────────────────────────────────────────────────────────
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""        # for GBP OAuth callback

    # ── GA4 ───────────────────────────────────────────────────────────────────
    GA4_PROPERTY_ID: str = ""            # e.g. "properties/123456789"
    GA4_REDIRECT_URI: str = ""           # for GA4 OAuth callback (can reuse GOOGLE_REDIRECT_URI)

    # ── Google Service Account (GBP API) ─────────────────────────────────────
    GOOGLE_SERVICE_ACCOUNT_JSON: str = "credentials/google-service-account.json"
    GOOGLE_BUSINESS_ACCOUNT_ID: str = ""
    GOOGLE_BUSINESS_LOCATION_ID: str = ""
    GOOGLE_WEBHOOK_SHARED_SECRET: str = ""
    GOOGLE_WEBHOOK_OIDC_AUDIENCE: str = ""
    GOOGLE_WEBHOOK_SERVICE_ACCOUNT_EMAIL: str = ""

    # ── Meta ──────────────────────────────────────────────────────────────────
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    BACKEND_CORS_ORIGINS: list[str] = Field(default_factory=list)

    # ── Admin ─────────────────────────────────────────────────────────────────
    ADMIN_SERVICE_TOKEN: str = ""
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

    # ── Storage ───────────────────────────────────────────────────────────────
    STORAGE_BACKEND: str = "local"   # local | s3 | gcs
    STORAGE_BUCKET: str = ""
    STORAGE_BASE_PATH: str = "uploads"

    # ── Rate limiting ──────────────────────────────────────────────────────────
    RATE_LIMIT_PER_TENANT_PER_MINUTE: int = 60
    MAX_PUSH_PER_DAY: int = 3

    # ── Payment (Newebpay) ────────────────────────────────────────────────────
    NEWEBPAY_MERCHANT_ID: str = ""
    NEWEBPAY_HASH_KEY: str = ""
    NEWEBPAY_HASH_IV: str = ""
    NEWEBPAY_API_URL: str = "https://core.newebpay.com/MPG/mpg_gateway"

    # ── Feature flags ──────────────────────────────────────────────────────────
    FEATURE_GA4: bool = False
    FEATURE_META: bool = False
    FEATURE_CROSS_CHANNEL: bool = False
    FEATURE_CRM: bool = False

    # ── Dangerous overrides (tests / local dev only) ──────────────────────────
    DANGEROUSLY_SKIP_SIGNATURE_CHECK: bool = False
    DANGEROUSLY_ALLOW_ALL_ORIGINS: bool = False
    ALLOW_SCHEMA_CREATE_IN_PRODUCTION: bool = False

    # ── AgentOS integration (v2-specific) ─────────────────────────────────────
    AGENTOS_BASE_URL: str = "http://localhost:8000"
    KACHU_BASE_URL: str = "http://localhost:8001"
    # Single-tenant shortcut: if set, Google webhook / scheduler use this tenant ID
    # instead of querying the DB for all active tenants.
    DEFAULT_TENANT_ID: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    def validate_production_config(self) -> None:
        """Raise if required production fields are missing."""
        if self.APP_ENV == "production":
            required = [
                ("SECRET_KEY", self.SECRET_KEY),
                ("TOKEN_ENCRYPTION_KEY", self.TOKEN_ENCRYPTION_KEY),
                ("LINE_CHANNEL_SECRET", self.LINE_CHANNEL_SECRET),
                ("LINE_CHANNEL_ACCESS_TOKEN", self.LINE_CHANNEL_ACCESS_TOKEN),
                ("GOOGLE_AI_API_KEY or OPENAI_API_KEY", self.GOOGLE_AI_API_KEY or self.OPENAI_API_KEY),
            ]
            if self.FEATURE_META:
                required.extend([
                    ("META_APP_ID", self.META_APP_ID),
                    ("META_APP_SECRET", self.META_APP_SECRET),
                ])
            if self.NEWEBPAY_MERCHANT_ID:
                required.extend([
                    ("NEWEBPAY_HASH_KEY", self.NEWEBPAY_HASH_KEY),
                    ("NEWEBPAY_HASH_IV", self.NEWEBPAY_HASH_IV),
                ])
            if self.ADMIN_EMAIL or self.ADMIN_PASSWORD:
                required.extend([
                    ("ADMIN_EMAIL", self.ADMIN_EMAIL),
                    ("ADMIN_PASSWORD", self.ADMIN_PASSWORD),
                ])
            if self.OAUTH_STATE_STORE_BACKEND == "memory":
                required.append(("OAUTH_STATE_STORE_BACKEND(not memory)", ""))
            missing = [name for name, val in required if not val]
            if missing:
                raise RuntimeError(f"Missing required production config: {missing}")
        if self.OAUTH_STATE_STORE_BACKEND not in {"auto", "redis", "memory"}:
            raise RuntimeError("OAUTH_STATE_STORE_BACKEND must be one of: auto, redis, memory")
        if self.OAUTH_STATE_TTL_SECONDS <= 0:
            raise RuntimeError("OAUTH_STATE_TTL_SECONDS must be greater than 0")


@lru_cache
def get_settings() -> Settings:
    return Settings()
