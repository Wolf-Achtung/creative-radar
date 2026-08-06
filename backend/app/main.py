import asyncio
import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.config import settings
from app.database import create_db_and_tables
from app.auth import auth_middleware
from app.user_session import user_auth_middleware
from app.api import health, channels, titles, posts, assets, reports, monitor, insights, proxy, admin, admin_users, cron, thumbnails, user_auth
from app.services.cron_scheduler import run_scheduler_loop

# Configure the root logger to stream INFO+ to stdout. Without this, Python's
# default "lastResort" handler only emits WARNING+ to stderr, so every
# ``logger.info(...)`` from PR #138 (brief_lock_attempt, brief_pipeline_*,
# etc.) was being silently dropped. Railway captures container stdout, so
# stream=sys.stdout is what gets us visibility in ``railway logs``. ``force=
# True`` overrides any handler uvicorn may have installed on the root logger
# before our import runs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Creative Radar API", version="1.0.0")

# Order: register auth_middleware FIRST so CORSMiddleware (added second) sits
# outermost and handles preflight before auth runs. Starlette executes
# middleware in reverse-add order on the request side.
#
# Sprint User-Login 2026-07: user_auth_middleware wird VOR auth_middleware
# registriert und liegt damit INNERHALB der Bearer-Schicht — Reihenfolge
# auf dem Request-Pfad: CORS -> Bearer (richtiges Frontend?) -> User-
# Session (welcher freigeschaltete Nutzer?) -> Route. Master-Schalter
# USER_AUTH_ENABLED (Default aus, staged Rollout wie bei den anderen
# beiden Auth-Schichten).
app.middleware("http")(user_auth_middleware)
app.middleware("http")(auth_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # Sprint 28.05.2026 (Admin-Login): das Admin-Session-Cookie ist
    # HttpOnly + SameSite=Lax. Damit der Browser es bei Cross-Subdomain-
    # Aufrufen (Frontend app.creative-radar.de → Backend api.creative-
    # radar.de) ueberhaupt mitschickt, muss CORS Credentials-aware sein.
    # Production: Wolf setzt CORS_ORIGINS=https://app.creative-radar.de
    # (konkreter Origin statt "*"), damit der Browser den Cookie
    # akzeptiert — Spec: bei credentials=true darf Allow-Origin kein "*"
    # zurueckgeben. Starlette echoed den Request-Origin zurueck, wenn
    # die Liste "*" enthaelt — funktioniert, sollte aber in Production
    # eingeschraenkt sein. Login + Logout funktionieren auch ohne CORS
    # bei reinen-Bearer-Aufrufen (kein Cookie), die kommen ueber den
    # bestehenden Header-Pfad weiter durch.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    """Sicherheits-Audit 2026-07-01: Basis-Header, die auf keiner Antwort
    gesetzt waren. Bewusst ohne Content-Security-Policy — die eingebaute
    Swagger-UI unter /docs laedt Inline-Skripte/-Styles + CDN-Assets, eine
    CSP haette das ohne sorgfaeltige Whitelist gebrochen. Registriert NACH
    der CORSMiddleware, damit dieser Layer aussen liegt und die Header auch
    auf CORS-Preflight- und Auth-Fehlerantworten landen (Starlette baut die
    Middleware-Kette in umgekehrter add()-Reihenfolge auf)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


storage_candidates = [
    Path("storage"),
    Path("backend/storage"),
    Path(__file__).resolve().parents[2] / "storage",
]
storage_path = next((candidate for candidate in storage_candidates if candidate.exists()), storage_candidates[0])
storage_path.mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=str(storage_path)), name="storage")


@app.on_event("startup")
async def on_startup():
    create_db_and_tables()
    # Incident 2026-07-13: OPENAI_MODEL war in Railway auf "gpt-5.4-nano"
    # gesetzt, obwohl Code-Default und alle Pricing-Kommentare fuer
    # "gpt-5.4-mini" ausgelegt waren -- dieser Drift blieb eine ganze
    # Session lang unbemerkt, weil sich Railway-ENV-Werte nirgends im Log
    # zeigen. Ein Log-Eintrag beim Start macht das ab jetzt in jedem
    # Railway-Log-Sichtbarkeitsfenster sofort sichtbar, ohne dass jemand
    # manuell die ENV-Liste ziehen muss.
    # app_env/mock_external_apis/cron_scheduler stehen bewusst VORNE
    # (Staging-Setup 2026-08-06): beim ersten Staging-Deploy war APP_ENV
    # unbemerkt auf "production" geblieben — der Boot-Check war damit ein
    # No-Op und der Wochen-Cron lief an. Beides war nur indirekt am
    # "cron_scheduler.started" erkennbar. Diese Zeile beantwortet
    # "in welcher Umgebung laeuft dieser Container eigentlich?" auf einen
    # Blick, ohne die ENV-Liste zu ziehen.
    from app.services.cron_scheduler import is_scheduler_enabled  # noqa: PLC0415

    logger.info(
        "startup.resolved_config app_env=%s mock_external_apis=%s "
        "cron_scheduler=%s storage_backend=%s openai_model=%s "
        "anthropic_sonnet_model=%s auth_enabled=%s admin_auth_enabled=%s",
        settings.app_env,
        settings.mock_external_apis,
        "on" if is_scheduler_enabled() else "off",
        settings.storage_backend,
        settings.openai_model,
        settings.anthropic_sonnet_model,
        settings.auth_enabled,
        settings.admin_auth_enabled,
    )
    # Incident 2026-07-13: der bisherige alleinige Trigger (GitHub Actions
    # Schedule) feuerte woechentlich 1,5-4,5h zu spaet. Der Loop unten laeuft
    # im selben Prozess und pollt die Uhrzeit selbst -- keine externe
    # Runner-Queue mehr im Trigger-Pfad. Referenz auf app.state, damit die
    # Task nicht vom GC eingesammelt wird (asyncio-Empfehlung fuer
    # "fire-and-forget"-Tasks).
    app.state.cron_scheduler_task = asyncio.create_task(run_scheduler_loop())


app.include_router(health.router)
app.include_router(channels.router)
app.include_router(titles.router)
app.include_router(posts.router)
app.include_router(assets.router)
app.include_router(reports.router)
app.include_router(monitor.router)
app.include_router(insights.router)
app.include_router(insights.pairs_router)
app.include_router(insights.roundups_router)
app.include_router(proxy.router)
app.include_router(admin.router)
app.include_router(admin.login_router)
app.include_router(admin_users.router)
app.include_router(admin_users.usage_router)
app.include_router(user_auth.router)
app.include_router(cron.router)
app.include_router(thumbnails.router)
