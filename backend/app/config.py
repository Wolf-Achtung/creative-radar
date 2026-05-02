from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "production"
    app_name: str = "creative-radar"
    database_url: str = ""
    database_private_url: str = ""
    database_public_url: str = ""
    allow_sqlite_fallback: bool = False

    pghost: str | None = None
    pgport: str | None = None
    pguser: str | None = None
    pgpassword: str | None = None
    pgdatabase: str | None = None

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    perplexity_api_key: str | None = None
    perplexity_model: str = "sonar-pro"
    tmdb_api_key: str | None = None
    tmdb_read_access_token: str | None = None
    apify_api_token: str | None = None
    apify_instagram_actor_id: str = "apify~instagram-scraper"
    apify_tiktok_actor_id: str = "clockworks~tiktok-scraper"
    apify_results_limit_per_channel: int = 5
    apify_wait_seconds: int = 60
    frontend_url: str = "*"
    cors_origins: str = "*"
    backend_url: str = ""
    report_timezone: str = "Europe/Berlin"

    secure_storage_enabled: bool = False

    storage_backend: str = "local"  # "local" | "s3"
    s3_bucket: str | None = None
    s3_region: str = "auto"
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_signed_url_ttl_seconds: int = 3600

    # Cost-logging (Phase 4 W4 Task 4.4 / F0.6). Logging only — no hard cap
    # (Wolf decision: Phase 5+).
    # USD->EUR conversion is static per Wolf-decision; adaptable via Railway
    # ENV without a code deploy.
    usd_to_eur_rate: float = 0.92
    # Apify default pricing: ~ 0.4 USD per Compute Unit. Override via ENV
    # if Wolf negotiates a different rate.
    apify_compute_unit_usd: float = 0.4
    # OpenAI gpt-4o-mini Vision/Text pricing per 1k tokens (USD).
    # Source: openai.com/api/pricing as of 2026-04. Update if model changes.
    openai_input_per_1k_usd: float = 0.000150
    openai_output_per_1k_usd: float = 0.000600

    # Bearer-token auth (Phase 4 W4 Task 4.3). Default off so the rollout can
    # land Frontend changes first; Wolf flips AUTH_ENABLED=true once both
    # Netlify and Railway carry the matching token. Public-path whitelist
    # lives in app/auth.py — this flag is the on/off switch only.
    auth_enabled: bool = False
    api_token: str | None = None

    image_proxy_allowed_hosts: str = (
        "cdninstagram.com,fbcdn.net,tiktokcdn.com,tiktokcdn-us.com,tiktokcdn-eu.com"
    )
    image_proxy_timeout_seconds: float = 8.0
    image_proxy_max_bytes: int = 8 * 1024 * 1024  # 8 MiB

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def allowed_origins(self) -> list[str]:
        raw = self.cors_origins or self.frontend_url or "*"
        if raw.strip() == "*":
            return ["*"]
        return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]

    @property
    def image_proxy_host_suffixes(self) -> list[str]:
        return [item.strip().lower().lstrip(".") for item in self.image_proxy_allowed_hosts.split(",") if item.strip()]


settings = Settings()
