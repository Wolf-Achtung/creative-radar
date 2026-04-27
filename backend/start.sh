#!/usr/bin/env sh
set -eu

APP_PORT="${PORT:-8000}"
echo "Starting Creative Radar API on port ${APP_PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT}"
