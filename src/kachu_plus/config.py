from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://kachu_plus:kachu_plus@localhost:5432/kachu_plus"
    AGENTOS_BASE_URL: str = "http://localhost:8000"
    AGENTOS_AUTO_RUN_EXECUTE_TASKS: bool = True
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_WEBHOOK_VERIFY_TOKEN: str = ""
    META_OAUTH_REDIRECT_URI: str = ""
    META_OAUTH_STATE_SECRET: str = ""
    META_OAUTH_SCOPES: str = "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish"
    GOOGLE_SERVICE_ACCOUNT_JSON: str = ""
    GOOGLE_BUSINESS_ACCOUNT_ID: str = ""
    GOOGLE_BUSINESS_LOCATION_ID: str = ""
    KACHU_BASE_URL: str = "http://localhost:8001"
    LINE_CHANNEL_ACCESS_TOKEN: str = ""
    LINE_PUSH_MIN_INTERVAL_SECONDS: float = 0.0
    META_GRAPH_MIN_INTERVAL_SECONDS: float = 0.0
    GOOGLE_BUSINESS_MIN_INTERVAL_SECONDS: float = 0.0
    AGENTOS_APPROVAL_SYNC_ENABLED: bool = True
    AGENTOS_APPROVAL_SYNC_INTERVAL_SECONDS: int = 60
    SLEEP_SYNC_ENABLED: bool = True
    SLEEP_SYNC_RUN_ON_STARTUP: bool = True
    SLEEP_SYNC_INTERVAL_SECONDS: int = 86400
    LITELLM_MODEL: str = "gemini/gemini-2.0-flash"
    CONSULTANT_LLM_MODEL: str = "gemini/gemini-2.0-flash"
    GOOGLE_AI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    # Admin API：Bearer token 保護，設定後 POST /admin/tenants 等才可用
    ADMIN_API_TOKEN: str = ""
    # 欄位加密：Fernet key（base64 url-safe 32 bytes）；空字串表示不加密（dev/test 環境可不設）
    FIELD_ENCRYPTION_KEY: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
