from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import create_db_and_tables
from app.api import health, channels, titles, posts, assets, reports, monitor, insights

app = FastAPI(title="Creative Radar API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
