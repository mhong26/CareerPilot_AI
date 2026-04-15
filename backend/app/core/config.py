from pydantic import field_validator
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

    # CORS — accepts comma-separated string or list
    cors_origins: list[str] = ["http://localhost:3000"]

    # AI (Google Gemini)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-pro"
    embedding_model: str = "text-embedding-004"

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    max_upload_size_mb: int = 10

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v


settings = Settings()
