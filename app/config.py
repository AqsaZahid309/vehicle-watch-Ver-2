from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_env: str = "development"
    secret_key: str = "change-me-to-a-long-random-secret-key"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # CORS — comma-separated list of allowed origins for production
    allowed_origins: list[str] = []

    # Database
    database_url: str = "postgresql+asyncpg://vehiclewatch:vehiclewatch@localhost:5432/vehiclewatch"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Gemini
    gemini_api_key: str = ""

    # Rate limiting
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Anomaly detection
    anomaly_worker_interval_seconds: int = 60
    anomaly_training_samples: int = 200
    anomaly_score_low: float = -0.1
    anomaly_score_medium: float = -0.3
    anomaly_score_critical: float = -0.5

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
