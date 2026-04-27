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
    apify_api_token: str | None = None
    apify_instagram_actor_id: str = "apify~instagram-scraper"
    apify_tiktok_actor_id: str = "clockworks~tiktok-scraper"
    apify_results_limit_per_channel: int = 5
    apify_wait_seconds: int = 60
    frontend_url: str = "*"
    cors_origins: str = "*"
    backend_url: str = ""
    report_timezone: str = "Europe/Berlin"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def allowed_origins(self) -> list[str]:
        raw = self.cors_origins or self.frontend_url or "*"
        if raw.strip() == "*":
            return ["*"]
        return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


settings = Settings()
