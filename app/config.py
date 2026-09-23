from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULT_SECRET = "change-me-to-a-long-random-secret-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # App — ENVIRONMENT is the documented name; APP_ENV kept for backwards compatibility.
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV"),
    )
    secret_key: str = _INSECURE_DEFAULT_SECRET
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    # When False, /auth/register is disabled and new users can only be created by an org admin.
    allow_public_signup: bool = True
    # SQLAlchemy statement logging — noisy, so opt-in only.
    sql_echo: bool = False

    # CORS — comma-separated list of allowed origins (used in production).
    # Stored as a plain string: pydantic-settings would otherwise expect JSON for list fields.
    allowed_origins_raw: str = Field(
        default="",
        validation_alias=AliasChoices("ALLOWED_ORIGINS", "ALLOWED_ORIGINS_RAW"),
    )

    # Database
    database_url: str = "postgresql+asyncpg://vehiclewatch:vehiclewatch@localhost:5432/vehiclewatch"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Gemini
    gemini_api_key: str = ""
    # "gemini-flash-latest" is Google's rolling alias, so the app does not break when a
    # pinned model version is retired (the reason gemini-1.5-flash stopped working).
    gemini_model: str = "gemini-flash-latest"
    gemini_timeout_seconds: float = 20.0

    # Rate limiting
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60
    login_rate_limit_requests: int = 10
    login_rate_limit_window_seconds: int = 60

    # Background worker
    # When True the worker loop runs inside the API process (single-container deploys).
    # Set to False when running `python -m app.workers.runner` as a separate service.
    run_worker_in_api: bool = True
    anomaly_worker_interval_seconds: int = 60
    # How far back the worker looks on its very first run for a device (no watermark yet).
    anomaly_backfill_minutes: int = 60

    # Anomaly detection
    anomaly_training_samples: int = 300
    anomaly_min_training_samples: int = 30
    anomaly_retrain_min_new_records: int = 300
    anomaly_retrain_min_interval_minutes: int = 60
    anomaly_drift_psi_threshold: float = 0.25
    anomaly_models_kept_per_device: int = 5
    # IsolationForest decision_function thresholds: 0 = contamination boundary.
    anomaly_score_low: float = -0.03
    anomaly_score_medium: float = -0.08
    anomaly_score_critical: float = -0.15
    # Alert only if at least MIN of the last WINDOW readings are abnormal.
    # Learning period: ML-only alerts are held back until a vehicle's model has this
    # many training samples. Safety-limit / OBD-II alerts are never held back.
    anomaly_mature_samples: int = 200
    anomaly_persistence_window: int = 5
    anomaly_persistence_min: int = 3

    # Trips, driver behaviour, fuel
    trip_gap_minutes: int = 5
    harsh_accel_ms2: float = 3.0
    harsh_brake_ms2: float = -3.5
    overspeed_kmh: float = 100.0
    over_rev_rpm: float = 3500.0
    fuel_theft_drop_pct: float = 8.0
    refuel_rise_pct: float = 10.0

    # Alert escalation
    escalation_minutes: int = 15

    # Data retention
    telemetry_retention_days: int = 90

    # Notifications — email (SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@vehiclewatch.local"
    smtp_starttls: bool = True

    # Notifications — SMS (Twilio REST API)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Public URL used in notification links
    public_base_url: str = "http://localhost:8000"

    # Observability — if set, /metrics requires `Authorization: Bearer <token>`
    metrics_token: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins_raw.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        if self.is_production and (
            self.secret_key == _INSECURE_DEFAULT_SECRET or len(self.secret_key) < 32
        ):
            raise ValueError(
                "SECRET_KEY must be set to a random value of at least 32 characters in production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
