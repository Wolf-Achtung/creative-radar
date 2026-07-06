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
    frontend_url: str = "https://app.creative-radar.de"
    # Sicherheits-Audit 2026-07-01: Default war zuvor "*". In Kombination mit
    # allow_credentials=True (main.py, fuer das Admin-Session-Cookie) haette
    # das jede beliebige Origin credentialed Cross-Origin-Requests machen
    # lassen (Starlette spiegelt bei allow_origins=["*"] den Request-Origin
    # zurueck, sobald allow_credentials=True gesetzt ist). Fester Produktions-
    # Origin als Default; via ENV weiterhin auf eine komma-separierte Liste
    # oder explizit "*" ueberschreibbar (siehe allowed_origins-Property).
    cors_origins: str = "https://app.creative-radar.de"
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
    # the alias form by default; override via Railway ENV to pin a specific
    # datestamp. Pricing per 1k tokens, source: platform.claude.com/docs/
    # en/about-claude/pricing as of 2026-07-01 (Sicherheits-Audit — this also
    # corrected the Opus row below, which had been carrying stale Opus-4/4.1-
    # era pricing 3x too high; see insight_engine.OPUS_MODEL_ALIAS for the
    # matching fix that actually wires this field into the brief-gen path).
    anthropic_api_key: str | None = None
    anthropic_haiku_model: str = "claude-haiku-4-5-20251001"
    anthropic_sonnet_model: str = "claude-sonnet-5"
    anthropic_haiku_input_per_1k_usd: float = 0.001
    anthropic_haiku_output_per_1k_usd: float = 0.005
    # Standard-Rate ab 01.09.2026; Sonnet 5 laeuft bis dahin mit Einfuehrungs-
    # preis $2/$10 pro Mtok (0.002/0.010 pro 1k) - guenstiger als hier
    # veranschlagt, die Cost-Logs ueberschaetzen also bis dahin leicht.
    anthropic_sonnet_input_per_1k_usd: float = 0.003
    anthropic_sonnet_output_per_1k_usd: float = 0.015
    # Opus 4.8 list price: $5/Mtok input, $25/Mtok output — identisch zu
    # Opus 4.5/4.6/4.7, die vorherigen $15/$75 hier waren falsch (das war
    # Opus-4/4.1-Alt-Pricing).
    anthropic_opus_model: str = "claude-opus-4-8"
    anthropic_opus_input_per_1k_usd: float = 0.005
    anthropic_opus_output_per_1k_usd: float = 0.025

    # Diagnose-Folge 2026-07-06 — bis dahin gab es kein Alerting auf den
    # woechentlichen Cron-Lauf: ein haengender/fehlgeschlagener Lauf fiel nur
    # auf, wenn zufaellig jemand aufs Dashboard schaute. Optionaler
    # Healthchecks.io-artiger Ping (Dead-Man's-Switch): leer = No-Op (Default).
    # Erfolg pingt die Basis-URL, Fehlschlag haengt ``/fail`` an (healthchecks.io-
    # Konvention) — der Dienst alarmiert dann sofort statt erst nach Ablauf
    # des Erwartungsfensters.
    cron_heartbeat_url: str | None = None

    # Bearer-token auth (Phase 4 W4 Task 4.3). Default off so the rollout can
    # land Frontend changes first; Wolf flips AUTH_ENABLED=true once both
    # Netlify and Railway carry the matching token. Public-path whitelist
    # lives in app/auth.py — this flag is the on/off switch only.
    auth_enabled: bool = False
    api_token: str | None = None

    # Sprint Security 16.06.2026 — dedizierter, eng gescopter Cron-Token.
    # Wird AUSSCHLIESSLICH auf ``/api/admin/cron/sync-all`` akzeptiert
    # (Least-Privilege: kann nur den Wochen-Sync ausloesen). Zweck:
    # entkoppelt den GitHub-Action-Wochenlauf vom rotationsempfindlichen
    # Haupt-``API_TOKEN`` — eine Rotation des Haupt-Tokens reisst den Cron
    # nicht mehr mit (Ausfall 15.06.). Optional: ist der Wert nicht gesetzt,
    # faellt der Cron auf den Haupt-Token zurueck (Backward-Compat). Rotation
    # siehe docs/ROTATION.md.
    cron_api_token: str | None = None

    # Sprint 28.05.2026 (Admin-Login) — zweite Auth-Schicht ueber dem
    # Bearer-Token. Bearer beantwortet "richtiges Frontend?", die
    # Admin-Session beantwortet "richtiger User?". Beide werden auf
    # User-Werkzeug-Routes (Treffer pruefen / Quellen / Report erstellen)
    # geprueft — defense-in-depth, kein Doppel-Schutz mit derselben
    # Frage.
    #
    # ``admin_password``: Klartext-Passwort, mit dem Wolf (oder ein
    # geteilter Account) sich einloggt. NIEMALS im Code/Chat
    # hardcoden — Wolf setzt das via Railway-ENV.
    # ``admin_session_secret``: HMAC-Schluessel fuer die Session-
    # Token-Signatur. Eigener ENV-Wert, damit ein Passwort-Wechsel
    # nicht alle ausgegebenen Tokens invalidiert (und damit man
    # rotieren kann ohne Login-Daten zu aendern). Wolf setzt das
    # einmal als ausreichend langen Random-String. Fehlt der Wert,
    # antwortet der Login-Endpoint mit 503 (fail-closed).
    # ``admin_session_ttl_seconds``: Default 8h = ein Arbeitstag.
    # ``admin_auth_enabled``: Master-Schalter; default off damit der
    # Rollout in Stages laufen kann (Backend deployen, dann Frontend,
    # dann ENVs setzen, dann auf True flippen).
    admin_password: str | None = None
    admin_session_secret: str | None = None
    admin_session_ttl_seconds: int = 8 * 3600
    admin_auth_enabled: bool = False

    # Sicherheits-Audit 2026-07-01 — Kill-Switch fuer das In-Memory-Rate-
    # Limiting (app/services/rate_limit.py). Default an; auf false setzen,
    # falls ein Incident (z. B. False-Positives durch geteiltes NAT) das
    # ohne Code-Deploy erfordert.
    rate_limit_enabled: bool = True

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

    # Vision-Backlog-Drain — neben den frisch erzeugten Assets zieht jeder
    # Cron-Lauf zusätzlich bis zu N älteste ``pending``-Assets nach, die nie
    # eine Vision-Analyse bekommen haben (feed-forward-Lücke + skipped_cap-
    # Überlauf). 200 * ~$0.015 = ~$3/Lauf, wöchentlich ~$13/Monat, gedeckelt.
    # 0 deaktiviert den Backlog-Drain (z.B. in Tests). Läuft NACH den
    # created_asset_ids; deren Selektion/Cap bleibt unberührt.
    cron_vision_backlog_max_assets_per_run: int = 200

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

    # Sprint 28.05.2026 (Evidenz-Block / Quellen-Attribution) —
    # Stufenmodell B→A fuer den Citation-Validator im Pair-Brief-Pfad.
    # Default ``False`` (Phase 1, Soft-Mode): das LLM ist im
    # System-Prompt zur Befuellung der ``cited_post_ids``-Felder
    # verpflichtet, der Validator loggt nicht-belegte IDs als
    # ``insight-engine-citation-unverified``, aber der Brief wird
    # trotzdem ausgeliefert. Cutover-Kriterium auf Phase 2 (Strikt):
    # nach 2-3 Wochen Cron-Lauf, Falsch-Zitat-Rate < 2 % im Log → ENV
    # ``INSIGHT_CITATION_STRICT_ENFORCE=true`` flippen, kein neuer
    # PR-Pfad. Strikt-Modus loest bei nicht-belegten IDs den
    # bestehenden Retry-Loop aus (MAX_RECALLS=2) und faellt im
    # Worst-Case auf ``llm_output=None`` + Persist-Skip zurueck.
    insight_citation_strict_enforce: bool = False

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

    # Cutter-Wochenbriefing (Master-Plan-Sprint 2026-06-12) — Schwellen der
    # deterministischen Evidenz-Pruefung (services/cutter_weekly.py). Ein
    # Muster gilt nur als gedeckt, wenn in der ISO-Woche pro Plattform
    # >= min_posts Posts mit ER >= rollender plattform-p75 ueber
    # >= min_distinct Distinct-Keys (Titel, sonst Pair/Channel) liegen.
    # Die p75 wird rollend ueber die letzten N Wochen aus der post-Tabelle
    # berechnet (NICHT aus den Top-N-Blobs — die sind vorgefiltert und
    # wuerden die Schwelle systematisch nach oben verzerren); unter
    # p75_min_sample Posts im Rollfenster ist die Schwelle undefiniert →
    # ehrlicher Leerlauf fuer die Plattform. Alle vier Werte sind bewusst
    # ENV-verstellbar: die Kalibrierung an echten Wochen (Trockenlauf)
    # justiert hier ohne Code-Deploy.
    cutter_weekly_min_posts: int = 5
    cutter_weekly_min_distinct_keys: int = 3
    cutter_weekly_p75_window_weeks: int = 8
    cutter_weekly_p75_min_sample: int = 30

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
