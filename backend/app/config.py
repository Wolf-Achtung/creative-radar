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
    # Total budget for one Apify actor run, in seconds. Was 60 (the
    # server-side ``waitForFinish`` cap) before the async-polling refactor;
    # is now the timeout enforced via ``asyncio.wait_for`` around the poll
    # loop, so it can safely be much larger than 60s. 1800s (30 min) covers
    # the long IG runs that previously returned raw_items=0 because the
    # dataset was read before the actor had finished.
    apify_wait_seconds: int = 1800
    # Interval between status polls of an in-flight Apify actor run.
    apify_poll_interval_seconds: int = 10

    # YouTube Data API v3 (Sprint 5.2.3). Free tier is 10k quota units/day;
    # one channel sync = 3 units (channels.list + playlistItems.list +
    # videos.list). Cost-log records units, not USD — see
    # record_youtube_api_call() in services/cost_log.py.
    youtube_api_key: str | None = None
    youtube_results_limit_per_channel: int = 10
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

    # Anthropic API (Sprint 5.3.1). Hybrid model strategy: Haiku for the
    # mechanical fields (format + tone), Sonnet for the contextual fields
    # (purpose + lifecycle_stage) and the vision call. Model strings are
    # the alias form by default — the alias auto-tracks the latest 4.5/4.6
    # release; override via Railway ENV to pin a specific datestamp.
    # Pricing per 1k tokens, source: anthropic.com/pricing as of 2026-05.
    anthropic_api_key: str | None = None
    anthropic_haiku_model: str = "claude-haiku-4-5-20251001"
    anthropic_sonnet_model: str = "claude-sonnet-4-6"
    anthropic_haiku_input_per_1k_usd: float = 0.001
    anthropic_haiku_output_per_1k_usd: float = 0.005
    anthropic_sonnet_input_per_1k_usd: float = 0.003
    anthropic_sonnet_output_per_1k_usd: float = 0.015
    # Opus 4.7 list price (anthropic.com/pricing, 2026-05): $15/Mtok input,
    # $75/Mtok output. The weekly-brief path (generate_weekly_report) pins
    # to claude-opus-4-7; override the alias via ENV to a datestamped pin
    # if Wolf wants to freeze a specific release.
    anthropic_opus_model: str = "claude-opus-4-7"
    anthropic_opus_input_per_1k_usd: float = 0.015
    anthropic_opus_output_per_1k_usd: float = 0.075

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

    # Sprint Beta — cap on the number of vision calls per cron run.
    # 50 * ~$0.015 = ~$0.75 per run; ~10 runs/month = ~$7.50/month at the
    # default 3-day cadence. Wolf raises this in Railway ENV once stability
    # is verified. 0 disables the auto-vision step entirely.
    cron_vision_max_assets_per_run: int = 50

    # Sprint F0.6 Hard-Cap-Vollausbau — Apify-Monatsbudget. Versicherung
    # gegen Spike-Anomalien (Faktor 10+), kein Optimierungs-Hebel.
    # Default $50 nach Apify-Pay-per-Event-Cost-Tracking-Fix (PR #116):
    # Wolfs Apify-Console zeigte am 11.05.2026 ~$13.41/Monat reale Cost
    # (98% Pay-per-Event-Result-Events, IG dominiert, TT klein). $50
    # gibt ~4× Cushion für saisonale Spikes (Major-Theatrical-Releases),
    # Channel-Ausbau (heute +21 neue Channels) und Apify-Pricing-
    # Erhöhungen. Wolf kann via Railway-ENV jederzeit hochsetzen,
    # falls der Verbrauch dauerhaft Richtung $40 läuft.
    # Pre-PR-#116-Default war $200 — basierte auf einer 15×-falschen
    # Verbrauchs-Schätzung (alter Compute-Units-Bug ließ alles als 0
    # tracken; Wolf schätzte aus Apify-Console-Werten der vor-PPE-Ära).
    apify_monthly_budget_usd: float = 50.0
    apify_soft_warn_pct: float = 0.80
    apify_hard_cap_pct: float = 1.00
    # Kill-Switch: auf False setzen deaktiviert den Hard-Cap komplett —
    # für Notfall-Runs nach einem Bug-Fix, wenn die Spike-Kosten zwar
    # gelogged aber nicht repräsentativ sind. Default an, damit die
    # Versicherung im Normalbetrieb greift.
    apify_budget_enforced: bool = True

    # Sprint F0.7 Hard-Cap-Vollausbau (2026-05-25) — Anthropic-Monatsbudget.
    # Datenpunkte vor Launch: 17.05 Force-Lauf 9 Pairs = $17.27;
    # 25.05 Cadence-Lauf 8 Pairs (mit M2-Retry-Aufschlag warnerbros) = $17.90.
    # Hochrechnung auf wöchentlichen Cron-Rhythmus: ~$70-90/Monat.
    # Default $100 gibt ~5× Cushion über die beobachtete Wochenlast —
    # genug für Prompt-Erweiterungen, mehr Pairs, oder eine schlechte
    # Woche mit intermittenten JSON-Parse-Retries. Mechanik exakt analog
    # F0.6: ``compute_anthropic_monthly_spend`` summiert über alle fünf
    # ``anthropic_*``-Provider-Buckets (Opus-Brief + Haiku/Sonnet-Vision-
    # Post-Analyzer + Generic-Fallback), Pre-Flight im Cron bricht den
    # ganzen Run mit ``status='budget_exceeded'`` ab, Kill-Switch via ENV.
    anthropic_monthly_budget_usd: float = 100.0
    anthropic_soft_warn_pct: float = 0.80
    anthropic_hard_cap_pct: float = 1.00
    anthropic_budget_enforced: bool = True

    # Master-Plan-Schritt-4 (2026-05-25) — Roll-out-Steuerung der
    # Segment-Roundups im Montags-Cron. CSV der Segment-Werte, kommagetrennt.
    # Default: die vier datenreichen Segmente (Wolf 25.05.). UK ausgesetzt
    # bis das UK-Inventar waechst — Zuschalten spaeter = ENV-Edit, kein
    # Code-Deploy. Parser im Cron-Block (``_run_segment_roundups_after_briefs``)
    # tolerant: unbekannter Einzelwert → Warning-Log + skip; leerer oder
    # komplett unparsebarer Gesamtwert → ERROR-Log und kein Roundup-Lauf
    # (Wolf-Festlegung Ping 1: nicht still in "keine Roundups" kippen).
    cron_roundup_segments: str = (
        "us_major,us_independent,de_verleih,de_independent"
    )

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
