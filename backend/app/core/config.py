import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore"：root .env 由 docker-compose 與 backend 共用，含 POSTGRES_* /
    # VITE_* 等本 Settings 沒有的鍵；從 repo root 啟動（如 eval runner）時
    # env_file 會讀到它們，不 ignore 會直接 ValidationError。
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # Database
    database_url: str = "postgresql://careerpilot:careerpilot@localhost:5432/careerpilot"

    # JWT
    jwt_secret: str = "change-this-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # CORS — accepts comma-separated string
    cors_origins_str: str = Field(default="http://localhost:3000", alias="cors_origins")

    # AI (Google Gemini)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    # primary 完整失敗（retry / repair 皆盡）時整套重跑一次的備援模型（FR-58）。
    # fallback 選較強的 flash：free tier RPD 僅 20，只當救援預算、不當日常流量。
    # 用 3.6（非 3.5）：實測 3.5-flash 在 free tier 持續 429，3.6-flash 可正常服務。
    gemini_fallback_model: str = "gemini-3.6-flash"
    embedding_model: str = "gemini-embedding-001"
    # MRL 維度：gemini-embedding-001 預設 3072，縮到 768 對齊 vector(768) schema。
    embedding_dim: int = 768

    # Observability（LangSmith，FR-60）。未設定時 tracing 靜默停用。
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "careerpilot-ai"

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    max_upload_size_mb: int = 10

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_str.split(",") if origin.strip()]


settings = Settings()

# LangSmith SDK 只讀 os.environ，不讀 .env 檔；把 pydantic 讀到的設定回填進去。
# 必須在模組載入時做（不能放 FastAPI startup hook）：langsmith 內部以 lru_cache
# 快取環境變數，第一次被讀取後即定型。setdefault 讓真正的環境變數優先。
if settings.langsmith_tracing:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    if settings.langsmith_project:
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
