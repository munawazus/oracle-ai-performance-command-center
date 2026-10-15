from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="development", alias="ENVIRONMENT")
    app_name: str = "Oracle AI Performance Command Center"

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")

    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")
    jwt_algorithm: str = "HS256"
    jwt_access_token_ttl_minutes: int = Field(default=15, alias="JWT_ACCESS_TOKEN_TTL_MINUTES")
    jwt_refresh_token_ttl_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_TTL_DAYS")

    secret_encryption_key: str = Field(alias="SECRET_ENCRYPTION_KEY")

    cors_allowed_origins: str = Field(default="http://localhost:5173", alias="CORS_ALLOWED_ORIGINS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    login_max_attempts: int = Field(default=5, alias="LOGIN_MAX_ATTEMPTS")
    login_lockout_minutes: int = Field(default=15, alias="LOGIN_LOCKOUT_MINUTES")

    # Master Plan Phase 24 (security hardening): a floor applied to every /api request by
    # RateLimitMiddleware, on top of login's own tighter per-IP limit and the even tighter
    # per-route limits on expensive endpoints (AI, reports, bulk onboarding, integration test).
    api_rate_limit_per_minute: int = Field(default=300, alias="API_RATE_LIMIT_PER_MINUTE")
    max_request_body_bytes: int = Field(default=5_000_000, alias="MAX_REQUEST_BODY_BYTES")
    # Dev/test escape hatch only - production must never allow integration targets that
    # resolve to a private/loopback/link-local address (SSRF). Off by default.
    allow_internal_integration_targets: bool = Field(default=False, alias="ALLOW_INTERNAL_INTEGRATION_TARGETS")

    collector_interval_seconds: int = Field(default=15, alias="COLLECTOR_INTERVAL_SECONDS")
    collector_enabled: bool = Field(default=True, alias="COLLECTOR_ENABLED")

    # SQL Intelligence (master plan Phase 9, Enterprise Readiness Plan D.1) - a separate,
    # slower-interval collector: V$SQL/V$SQL_PLAN queries are the heaviest this platform
    # issues, and plan bodies don't change every 15s, so running them at the main collector's
    # interval would be wasteful. An independent enable flag lets an operator pause just this
    # collector without disabling session/wait/health collection.
    sql_collector_enabled: bool = Field(default=True, alias="SQL_COLLECTOR_ENABLED")
    sql_collector_interval_seconds: int = Field(default=300, alias="SQL_COLLECTOR_INTERVAL_SECONDS")
    sql_top_n: int = Field(default=50, ge=1, le=500, alias="SQL_TOP_N")
    # Privacy escape hatch: still track/rank/detect regressions on statements without
    # persisting their text, for an operator who can't retain query text at all.
    sql_capture_text: bool = Field(default=True, alias="SQL_CAPTURE_TEXT")

    # Capacity/tablespace collector (master plan Phase 5, Enterprise Readiness Plan D.2) - an
    # even slower interval than the SQL collector: tablespace growth is an hours-to-days
    # signal, not a minutes signal, so polling it every 300s would be 12x the row volume for
    # no benefit. An independent enable flag mirrors the SQL collector's own off-switch.
    capacity_collector_enabled: bool = Field(default=True, alias="CAPACITY_COLLECTOR_ENABLED")
    capacity_collector_interval_seconds: int = Field(default=3600, alias="CAPACITY_COLLECTOR_INTERVAL_SECONDS")
    # "How full is too full" varies by environment/disk-provisioning policy far more than a
    # SQL regression's ratio ladder does, so these are operator-tunable, not hardcoded.
    capacity_warn_pct_used: float = Field(default=85.0, alias="CAPACITY_WARN_PCT_USED")
    capacity_critical_pct_used: float = Field(default=95.0, alias="CAPACITY_CRITICAL_PCT_USED")
    capacity_warn_days_to_full: float = Field(default=30.0, alias="CAPACITY_WARN_DAYS_TO_FULL")
    capacity_critical_days_to_full: float = Field(default=7.0, alias="CAPACITY_CRITICAL_DAYS_TO_FULL")

    # Statspack integration (master plan Phase 11, Enterprise Readiness Plan D.3) - an even
    # slower interval than capacity's: Statspack snapshots are taken on the DBA's own
    # independent, irregular schedule (often hourly, sometimes not at all), so polling more
    # often than a typical DBA snap cadence wastes queries against PERFSTAT for no new data.
    # The sync itself is a lossless catch-up (never misses a snapshot), so a slower interval
    # only affects latency-to-visibility, never correctness.
    statspack_collector_enabled: bool = Field(default=True, alias="STATSPACK_COLLECTOR_ENABLED")
    statspack_collector_interval_seconds: int = Field(default=7200, alias="STATSPACK_COLLECTOR_INTERVAL_SECONDS")
    # Bounds one sync run's cost if a large backlog exists (e.g. the first sync after months
    # of untouched Statspack) - catches up gradually across ticks rather than in one round.
    statspack_max_snapshots_per_sync: int = Field(default=12, ge=1, le=100, alias="STATSPACK_MAX_SNAPSHOTS_PER_SYNC")

    # Change Intelligence (master plan Phase 14, Enterprise Readiness Plan D.4) - configuration
    # parameter drift detection. An hours-to-days signal like capacity, so it shares that same
    # interval tier by default - but more time-sensitive than Statspack's DBA-controlled
    # cadence, since ALTER SYSTEM can happen at any moment and correlating it with an incident
    # matters. An independent enable flag mirrors every other collector's own off-switch.
    config_collector_enabled: bool = Field(default=True, alias="CONFIG_COLLECTOR_ENABLED")
    config_collector_interval_seconds: int = Field(default=3600, alias="CONFIG_COLLECTOR_INTERVAL_SECONDS")

    notification_interval_seconds: int = Field(default=10, alias="NOTIFICATION_INTERVAL_SECONDS")
    frontend_base_url: str = Field(default="http://localhost:5173", alias="FRONTEND_BASE_URL")

    # AI gateway (app/ai/gateway.py). Off by default - no local model is assumed to exist.
    # Points at any OpenAI-compatible endpoint (vLLM/Ollama/llama.cpp all support this).
    ai_enabled: bool = Field(default=False, alias="AI_ENABLED")
    ai_base_url: str = Field(default="http://localhost:8080/v1", alias="AI_BASE_URL")
    ai_api_key: str = Field(default="not-needed", alias="AI_API_KEY")
    ai_primary_model: str = Field(default="gpt-oss-120b", alias="AI_PRIMARY_MODEL")
    ai_fallback_model: str = Field(default="gpt-oss-20b", alias="AI_FALLBACK_MODEL")
    ai_request_timeout_seconds: int = Field(default=30, alias="AI_REQUEST_TIMEOUT_SECONDS")
    # A separate, smaller/faster model for the conversational Copilot chat path - RCA keeps
    # ai_primary_model unchanged (its JSON-schema reliability is real, benchmark-scored work,
    # not worth risking for a chat feature that doesn't even use json_mode). Falls back to
    # ai_primary_model, not ai_fallback_model, if this dedicated model is unavailable - the
    # already-proven model is a better safety net than the RCA fallback.
    ai_copilot_model: str = Field(default="llama3.2:3b", alias="AI_COPILOT_MODEL")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values are loaded from the environment/.env
