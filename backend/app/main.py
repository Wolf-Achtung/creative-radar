import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.config import settings
from app.database import create_db_and_tables
from app.auth import auth_middleware
from app.api import health, channels, titles, posts, assets, reports, monitor, insights, proxy, admin, cron, thumbnails

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

app = FastAPI(title="Creative Radar API", version="1.0.0")

# Order: register auth_middleware FIRST so CORSMiddleware (added second) sits
# outermost and handles preflight before auth runs. Starlette executes
# middleware in reverse-add order on the request side.
app.middleware("http")(auth_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage_candidates = [
    Path("storage"),
    Path("backend/storage"),
    Path(__file__).resolve().parents[2] / "storage",
]
storage_path = next((candidate for candidate in storage_candidates if candidate.exists()), storage_candidates[0])
storage_path.mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=str(storage_path)), name="storage")


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


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
app.include_router(cron.router)
app.include_router(thumbnails.router)
