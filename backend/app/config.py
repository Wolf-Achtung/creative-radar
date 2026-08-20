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
    # 2026-07-06: switched from gpt-4o-mini to gpt-5.4-mini on request.
    openai_model: str = "gpt-5.4-mini"
    # STAGING_EXPECTED_DB_HOST (Staging-Briefing 2026-08-06): Whitelist-
    # Boot-Check. Bei APP_ENV=staging MUSS dieser Wert gesetzt sein und
    # im aufgeloesten DATABASE_URL vorkommen, sonst verweigert der Boot
    # (app/database.py::_guard_staging_database). Verhindert die
    # schlimmste Verwechslung: ein Staging-Service, der versehentlich
    # mit der Prod-DB-URL konfiguriert wurde. Whitelist statt Blacklist,
    # damit auch "Variable vergessen" auffaellt statt durchzurutschen.
    staging_expected_db_host: str | None = None

    # MOCK_EXTERNAL_APIS (Staging-Briefing 2026-08-06): die drei Scrape-
    # Connectors (Apify IG/TT, YouTube) liefern deterministische Fixtures
    # aus services/mock_fixtures.py statt echter HTTP-Calls, und ihre
    # is_*_configured()-Gates melden True ohne Token. Fuer lokale Dev
    # (docker-compose) und Cloud-Staging — Prod laesst den Default stehen.
    # LLM-Dienste (OpenAI/Anthropic) sind bewusst nicht erfasst: ohne Key
    # degradieren deren Call-Sites sauber, Briefs kommen aus seed_dev.
    mock_external_apis: bool = False

    # PERPLEXITY_API_KEY/_MODEL: entfernt im Kosten-Audit 2026-08-01.
    # ``services/market_context.py`` war der einzige Nutzer und hatte seit
    # der Diagnose 2026-05-08 keinen einzigen Aufrufer mehr — 0 Calls,
    # 0 USD, keine costlog-Zeile. Die Railway-Variablen koennen weg.
    # (Nicht betroffen: ``CandidateSource.PERPLEXITY`` und
    # ``scripts/import_channels.py`` — das ist der manuelle CSV-Seed aus
    # einer Perplexity-Recherche, kein API-Zugriff.)
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
    # OpenAI gpt-5.4-mini Vision/Text pricing per 1k tokens (USD).
    # 2026-07-06: switched from gpt-4o-mini. MEDIUM-HIGH CONFIDENCE (re-audit
    # 2026-07-13) — openai.com/api/pricing still 403s from this sandbox, but
    # several independent third-party sources (incl. OpenRouter, which pulls
    # pricing programmatically from provider APIs) now converge unanimously on
    # $0.75/$4.50 per 1M tokens with no outliers. Still no first-party source
    # confirmed; verify against platform.openai.com/docs/pricing (or an actual
    # billed API response) before trusting cost reports built on this.
    openai_input_per_1k_usd: float = 0.00075
    openai_output_per_1k_usd: float = 0.0045

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
    # Wartung 20.08.2026: von 0.003/0.015 auf 0.002/0.010 korrigiert.
    # Hier stand die erwartete Standard-Rate ab 01.09.2026 ($3/$15 pro
    # MTok); der Einfuehrungspreis $2/$10 galt als befristet. Anthropic
    # hat die Erhoehung abgesagt. Preisseite, abgerufen 20.08.2026:
    #
    #   "The $2/$10 per million input/output token pricing for Claude
    #    Sonnet 5, announced at launch as introductory pricing through
    #    August 31, 2026, is now the standard price. The previously
    #    scheduled increase to $3/$15 per million input/output tokens on
    #    September 1, 2026 will not occur."
    #
    # Bis zu dieser Korrektur haben die Cost-Logs den Sonnet-Anteil um
    # 50 % ueberschaetzt — betrifft den Post-Analyzer, den einzigen
    # Sonnet-Pfad. Der Anthropic-Monatsdeckel hat dadurch zu frueh
    # gebremst, nicht zu spaet; ein Kosten-Risiko bestand nicht.
    anthropic_sonnet_input_per_1k_usd: float = 0.002
    anthropic_sonnet_output_per_1k_usd: float = 0.010
    # Opus 4.8 list price: $5/Mtok input, $25/Mtok output — identisch zu
    # Opus 4.5/4.6/4.7, die vorherigen $15/$75 hier waren falsch (das war
    # Opus-4/4.1-Alt-Pricing).
    anthropic_opus_model: str = "claude-opus-4-8"
    anthropic_opus_input_per_1k_usd: float = 0.005
    anthropic_opus_output_per_1k_usd: float = 0.025

    # ``ANTHROPIC_PROMPT_CACHING``: Kill-Switch fuer den Cache-Breakpoint am
    # Ende des System-Prompts in ``services/anthropic_client.py``. Default an.
    # Auf ``false`` gesetzt geht exakt das vorherige Request-Format raus —
    # ``system`` als String, kein ``cache_control``.
    anthropic_prompt_caching: bool = True

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

    # Staging-Abnahme 2026-08-06: ``/docs``, ``/redoc`` und ``/openapi.json``
    # standen seit Phase 4 auf der Public-Whitelist ("stays open in pilot").
    # Im Staging-Log war zu sehen, dass Scanner-Bots das binnen Minuten nach
    # der Zertifikatsausstellung abgreifen — /openapi.json war die einzige
    # 200er-Antwort in einer Flut von 401ern. Das ist keine Luecke (die
    # Endpunkte selbst bleiben token-geschuetzt), aber eine vollstaendige
    # Landkarte der API fuer jeden Fremden. Die Pilotphase ist vorbei.
    #
    # Default zu: in Production liefert /openapi.json ab jetzt 401. Auf
    # Staging/lokal ``DOCS_PUBLIC=true`` setzen — nur so ist die Swagger-UI
    # im Browser nutzbar, weil sie ihr Schema per fetch() ohne Header laedt.
    # Mit Bearer-Token bleibt /openapi.json in JEDER Umgebung erreichbar
    # (curl -H "Authorization: Bearer ..."), es geht also nichts verloren.
    docs_public: bool = False

    # Sprint Security 16.06.2026 — dedizierter, eng gescopter Cron-Token.
    # Wird AUSSCHLIESSLICH auf ``/api/admin/cron/sync-all`` akzeptiert
    # (Least-Privilege: kann nur den Wochen-Sync ausloesen). Zweck:
    # entkoppelt den GitHub-Action-Wochenlauf vom rotationsempfindlichen
    # Haupt-``API_TOKEN`` — eine Rotation des Haupt-Tokens reisst den Cron
    # nicht mehr mit (Ausfall 15.06.). Audit 2026-08-17: der fruehere
    # Fallback auf den Haupt-Token ist abgeschafft — das Haupt-``API_TOKEN``
    # liegt im oeffentlichen Frontend-Bundle und berechtigt nicht mehr zum
    # Ausloesen des teuersten Endpoints (siehe app/api/cron.py,
    # ``require_cron_trigger_auth``). Rotation siehe docs/ROTATION.md.
    cron_api_token: str | None = None

    # Audit 2026-08-17: Boot-Check in main.py. Production mit deaktivierter
    # Auth (AUTH_ENABLED/ADMIN_AUTH_ENABLED) bricht den Start ab, statt die
    # API still offen zu servieren. true = bewusster Override, z.B. waehrend
    # eines Incidents am Auth-Code selbst.
    allow_auth_disabled_in_production: bool = False

    # Audit 2026-08-17 (SSRF): der Link-Preview-Fetch in
    # services/link_preview.py folgt nur noch Zielen auf diesen Host-
    # Suffixen (gleiche Semantik wie IMAGE_PROXY_ALLOWED_HOSTS). Alles
    # andere — insbesondere Railway-interne Hosts — wird nicht gefetcht,
    # der Import faellt auf "link-only" zurueck.
    link_preview_allowed_hosts: str = "instagram.com,instagr.am"

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

    # Sprint 2026-07-21 (Wolf): Login-User, die automatisch Voll-Admin
    # sind — komma-separierte E-Mail-Liste (Railway-ENV
    # ADMIN_USER_EMAILS=wolf@trailerhaus.de). Wer hier steht und per
    # E-Mail-Code eingeloggt ist, erreicht den Admin-Bereich ohne das
    # separate ADMIN_PASSWORD. Bewusst ENV statt DB-Flag: die Rechte
    # lassen sich nicht ueber die Oberflaeche vergeben (kein
    # Selbst-Eskalations-Pfad), Aenderung = ENV-Edit ohne Deploy.
    # Das Passwort-Login unter /admin bleibt parallel bestehen
    # (Fallback, falls das Mail-Login mal klemmt).
    admin_user_emails: str = ""

    # Sicherheits-Audit 2026-07-01 — Kill-Switch fuer das In-Memory-Rate-
    # Limiting (app/services/rate_limit.py). Default an; auf false setzen,
    # falls ein Incident (z. B. False-Positives durch geteiltes NAT) das
    # ohne Code-Deploy erfordert.
    rate_limit_enabled: bool = True

    # Sprint User-Login 2026-07 — E-Mail+Code-Login fuer ~15 bekannte
    # Nutzer (Team/Kunden), Flow identisch zum Referenzprojekt
    # api-ki-backend-neu (6-stelliger Code, 10 Min. gueltig, Einmal-
    # Nutzung; Rate-Limits 10x Code-Anfordern / 5x Login pro 5 Min.).
    #
    # ``user_auth_enabled``: Master-Schalter (Rollout-Muster wie
    # admin_auth_enabled: Backend deployen, Frontend deployen, ENVs
    # setzen, dann flippen). Solange False, bleibt die gesamte Website
    # oeffentlich wie bisher.
    # ``user_session_secret``: HMAC-Schluessel fuer die Session-Token-
    # Signatur (eigener Wert, NICHT admin_session_secret wiederverwenden
    # — getrennte Rotation). Wolf setzt: ``openssl rand -hex 32``.
    # ``user_session_ttl_seconds``: Default 30 Tage — die Nutzer oeffnen
    # Wochen-Briefs, ein woechentlicher Re-Login waere reine Reibung.
    # Bewusste Abweichung von der 1h-JWT-TTL der Referenz ("Angemeldet
    # bleiben" ist hier der eingebaute Normalfall statt einer Checkbox).
    # ``login_code_ttl_seconds``: 600 = 10 Minuten, identisch Referenz.
    user_auth_enabled: bool = False
    user_session_secret: str | None = None
    user_session_ttl_seconds: int = 30 * 24 * 3600
    login_code_ttl_seconds: int = 600
    auth_rate_window_sec: int = 300
    auth_rate_max_request_code: int = 10
    auth_rate_max_login: int = 5

    # E-Mail-Versand fuer Login-Codes. Provider-Weiche wie im Referenz-
    # projekt: "resend" (Default; braucht RESEND_API_KEY + MAIL_FROM mit
    # in Resend verifizierter Absender-Domain) oder "smtp" (klassisch,
    # braucht SMTP_HOST/SMTP_USER/SMTP_PASSWORD/MAIL_FROM).
    # ``disable_emails=True`` ist der Kill-Switch fuer Tests/lokale Devs
    # — Versand wird geloggt statt ausgefuehrt.
    email_provider: str = "resend"
    resend_api_key: str | None = None
    mail_from: str | None = None
    mail_from_name: str = "Creative Radar"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    mail_timeout_seconds: float = 30.0
    disable_emails: bool = False

    # Playbook-Montags-Mail (Hook-Intelligence/Radar, 20.08.2026):
    # Komma-Liste der Empfaenger. Leer = kein Versand (der Cron-Block
    # ueberspringt still mit Log) — bewusst KEIN Default auf Wolfs
    # Adresse, Empfaenger sind eine Deploy-Entscheidung.
    playbook_mail_recipients: str = ""

    image_proxy_allowed_hosts: str = (
        "cdninstagram.com,fbcdn.net,tiktokcdn.com,tiktokcdn-us.com,tiktokcdn-eu.com"
    )
    image_proxy_timeout_seconds: float = 8.0
    image_proxy_max_bytes: int = 8 * 1024 * 1024  # 8 MiB

    # Vision-Deckel je Cron-Lauf: frisch erzeugte Assets.
    #
    # Wartung 20.08.2026: von 50 auf 800 angehoben. Der alte Wert stammte
    # aus dem Sprint-Beta-Plan und war an ``50 * ~$0.015 = ~$0.75 pro
    # Lauf`` kalibriert — an einem Vision-Preis aus der gpt-4o-mini-Zeit,
    # der seit dem Modellwechsel um Faktor 5,6 zu hoch ist (gemessen
    # ~$0,0027, siehe api/cron.py::_VISION_COST_USD_PER_CALL).
    #
    # Der Deckel wurde nie nachgezogen und war damit die Ursache eines
    # stillen Datenverlusts: bei ~680 neuen Assets pro Woche liessen 50
    # frische + 200 Backlog rund zwei Drittel liegen. Gemessen am
    # 20.08.2026 ueber 90 Tage: 3.228 Assets ``pending`` (nie
    # angefasst), 3.023 ``analyzed``, 2.338 ``fetch_failed``. Der Stapel
    # wuchs schneller, als er abgearbeitet wurde — und er verfaellt
    # dabei, weil Instagram-CDN-Links nach 24-48h ablaufen. Was nicht
    # zeitnah analysiert wird, ist danach nicht mehr analysierbar.
    #
    # 800 deckt den woechentlichen Zufluss mit Reserve. Die Kosten sind
    # dabei nicht mehr die bindende Groesse: 800 * ~$0,0027 = ~$2,20 pro
    # Lauf gegen einen Monatsdeckel von $50. Begrenzt wird der Lauf jetzt
    # ueber die ZEIT (``VISION_STAGE_TIMEOUT_SECONDS``, Default 1800s,
    # geteilt mit dem Backlog-Drain) — was nicht mehr hineinpasst, bleibt
    # liegen und erscheint als ``skipped_budget`` im Cron-Summary.
    # 0 deaktiviert den Schritt.
    cron_vision_max_assets_per_run: int = 800

    # Vision-Backlog-Drain — neben den frisch erzeugten Assets zieht jeder
    # Cron-Lauf zusätzlich bis zu N älteste ``pending``-Assets nach, die nie
    # eine Vision-Analyse bekommen haben (feed-forward-Lücke + skipped_cap-
    # Überlauf). Läuft NACH den created_asset_ids; deren Selektion/Cap
    # bleibt unberührt. 0 deaktiviert den Drain (z.B. in Tests).
    #
    # Wartung 20.08.2026: von 200 auf 800. Gleiche Begruendung wie oben.
    # Der Altbestand von ~3.200 ``pending``-Assets braucht bei 800 pro
    # Lauf rund vier Woechen — sofern das Zeitbudget reicht; sonst
    # dauert es entsprechend laenger, aber sichtbar statt still.
    cron_vision_backlog_max_assets_per_run: int = 800

    # Post-Analyse-Drain (Trailer-Intelligence Stufe 1) — pro Cron-Lauf bis
    # zu N Posts ohne ``last_analyzed_at`` klassifizieren (format / tone /
    # purpose / lifecycle_stage). Die Inventur vom 06.08.2026 fand nur 920
    # von 7.623 Posts klassifiziert (12%), weil der Analyzer bis dahin
    # ausschliesslich am manuellen Admin-Endpunkt hing. Leidtragender ist
    # der Empfehlungs-Baustein im insight_engine: er verlangt >= 3 Posts je
    # Cross-Tab-Wert und faellt bei 12% Abdeckung fuer die Dimensionen
    # ``format`` und ``lifecycle_stage`` regelmaessig unter diese Schwelle.
    #
    # 800 * ~$0.0029 (text-only, s.u.) = ~$2.30/Lauf, woechentlich ~$10/Monat.
    # Bewusst hoch genug, dass der laufende Zufluss (~600 neue Posts/Woche)
    # in EINEM Lauf durchgeht und der Alt-Bestand trotzdem abgebaut wird.
    # 0 deaktiviert die Stage. Zusaetzlich gedeckelt durch den bestehenden
    # Anthropic-Monatsbudget-Pre-Flight (``anthropic_monthly_budget_usd``).
    cron_post_analysis_max_posts_per_run: int = 800

    # Text-only: den Sonnet-Vision-Call in der Cron-Stage ueberspringen.
    # Der Vision-Call ist ~72% der Kosten pro Post (~$0.0073 von ~$0.0101),
    # liefert aber nur ``vision_description`` — ein Feld, das der
    # Empfehlungs-Baustein nicht liest. Die vier PostAnalysis-Felder kommen
    # aus der Caption und sind unberuehrt. Auf ``false`` setzen, wenn die
    # Vision-Beschreibungen spaeter flaechendeckend gebraucht werden; das
    # verdreifacht die Kosten dieser Stage. Der manuelle Admin-Endpunkt
    # laeuft unabhaengig davon weiter mit voller Pipeline.
    cron_post_analysis_skip_vision: bool = True

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

    # Incident 2026-07-13 (Re-Audit-Folgefund) — OpenAI hatte als einziger
    # der drei kostenpflichtigen Provider (Apify/Anthropic/OpenAI) gar
    # keinen Monatsbudget-Deckel. Beobachteter Verbrauch mit gpt-5.4-nano
    # lag bei ~$1-1,50 pro Wochenlauf (Vision + Caption-Analyse); der
    # Wechsel auf gpt-5.4-mini (bessere Instruktions-Befolgung/Bildver-
    # staendnis, siehe Modell-Vergleich in der Session) treibt das je nach
    # Aggregator-Pricing hoeher. $50 Default spiegelt den Apify-Cap-Wert
    # und gibt bei realistischer Hochrechnung (~$5-10/Woche) deutliche
    # Cushion. Mechanik exakt analog Apify/Anthropic:
    # ``compute_openai_monthly_spend`` summiert den ``openai``-Provider-
    # Bucket, Pre-Flight im Cron bricht den ganzen Run mit
    # ``status='budget_exceeded'`` ab, Kill-Switch via ENV.
    openai_monthly_budget_usd: float = 50.0
    openai_soft_warn_pct: float = 0.80
    openai_hard_cap_pct: float = 1.00
    openai_budget_enforced: bool = True

    # Sprint Review-Automatisierung 2026-07-20 — Kandidaten-Autopilot
    # (services/candidate_autopilot.py). Bestaetigt offene Titel-Vorschlaege
    # automatisch, wenn der vorgeschlagene Titel EXAKT und EINDEUTIG in der
    # Titel-Whitelist steht und die Matcher-Confidence >= min_confidence
    # ist (der 0.95-Auto-Match des Matchers bleibt unberuehrt — der
    # Autopilot deckt die 0.85-0.95-Luecke mit der zusaetzlichen
    # Exakt-Treffer-Bedingung ab). Alte schwache Kandidaten (aelter als
    # stale_days UND Confidence < stale_max_confidence) werden reversibel
    # auf IGNORED gesetzt. Kill-Switch + Schwellen via Railway-ENV.
    candidate_autopilot_enabled: bool = True
    candidate_autopilot_min_confidence: float = 0.85
    candidate_autopilot_stale_days: int = 28
    candidate_autopilot_stale_max_confidence: float = 0.5

    # Sprint 28.05.2026 (Evidenz-Block / Quellen-Attribution) —
    # Stufenmodell B→A fuer den Citation-Validator im Pair-Brief-Pfad.
    # Soft-Mode (``False``): das LLM ist im System-Prompt zur Befuellung
    # der ``cited_post_ids``-Felder verpflichtet, der Validator loggt
    # nicht-belegte IDs als ``insight-engine-citation-unverified``, der
    # Brief wird aber ausgeliefert. Strikt (``True``) verwirft die
    # Antwort, loest den Retry-Loop aus (MAX_RECALLS=2) und faellt im
    # Worst-Case auf ``llm_output=None`` + Persist-Skip zurueck — dann
    # gibt es fuer dieses Pair in dieser Woche gar keinen Brief.
    #
    # ---- Stand 20.08.2026: bleibt bewusst aus. ----
    #
    # Hier stand seit Mai das Cutover-Kriterium "nach 2-3 Wochen
    # Cron-Lauf, Falsch-Zitat-Rate < 2 % → flippen". Die Messung ist
    # nachgeholt (``scripts/diag_citation_rate.py``, read-only gegen den
    # DB-Bestand, kein Log noetig):
    #
    #   0,19 % ueber 120 Briefs und 4.186 zitierte IDs, W22 bis W33.
    #   8 nicht belegte IDs auf 6 Briefe verteilt; W31-W33 fehlerfrei.
    #   Auffaellig nur ``cross_market_insight`` (1,17 %) — die einzige
    #   Sektion, die ``match_key`` statt Post-URLs zitiert.
    #
    # Das Kriterium waere also zehnfach erfuellt. Trotzdem bleibt der
    # Schalter aus, aus einem Grund, den das Kriterium nicht kannte:
    # ``cited_post_ids`` wird im Frontend NIRGENDS gerendert (Stand
    # 20.08.2026 kein einziger Treffer in ``frontend/src``). Ein
    # falsches Zitat ist fuer den Report-Leser unsichtbar, ein
    # korrigiertes auch. Der Flip kauft also keine sichtbare Qualitaet,
    # riskiert aber einen fehlenden Brief — bei 6 von 120 Briefen im
    # Messzeitraum.
    #
    # Wieder aufmachen, wenn eins davon eintritt: die Rate steigt
    # spuerbar (Skript erneut laufen lassen), oder die Belege tauchen im
    # Report auf und werden damit fuer den Leser ueberpruefbar. Dann hat
    # der Strikt-Modus einen sichtbaren Zweck.
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
        # Audit 2026-08-17: "*" wird nicht mehr durchgereicht. Die App faehrt
        # allow_credentials=True (Session-Cookies) — mit allow_origins=["*"]
        # spiegelt Starlette den Request-Origin, und jede fremde Website
        # koennte credentialed Cross-Origin-Requests senden. Wildcard- und
        # Leer-Konfiguration fallen darum auf den festen Produktions-Origin
        # zurueck; mehrere Origins weiterhin als komma-separierte Liste.
        raw = self.cors_origins or self.frontend_url or ""
        origins = [
            item.strip().rstrip("/")
            for item in raw.split(",")
            if item.strip() and item.strip() != "*"
        ]
        if not origins:
            fallback = (self.frontend_url or "https://app.creative-radar.de").strip()
            return [fallback.rstrip("/")] if fallback != "*" else ["https://app.creative-radar.de"]
        return origins

    @property
    def admin_user_email_set(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower()
            for item in (self.admin_user_emails or "").split(",")
            if item.strip()
        )

    @property
    def image_proxy_host_suffixes(self) -> list[str]:
        return [item.strip().lower().lstrip(".") for item in self.image_proxy_allowed_hosts.split(",") if item.strip()]

    @property
    def link_preview_host_suffixes(self) -> list[str]:
        return [item.strip().lower().lstrip(".") for item in self.link_preview_allowed_hosts.split(",") if item.strip()]


settings = Settings()
