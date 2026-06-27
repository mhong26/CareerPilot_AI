from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

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
    gemini_model: str = "gemini-2.5-flash"
    embedding_model: str = "gemini-embedding-001"
    # MRL 維度：gemini-embedding-001 預設 3072，縮到 768 對齊 vector(768) schema。
    embedding_dim: int = 768

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    max_upload_size_mb: int = 10

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_str.split(",") if origin.strip()]


settings = Settings()
