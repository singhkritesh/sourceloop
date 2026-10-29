#!/usr/bin/env sh
# Local (non-Docker) API on port 8005. Usage: scripts/start.sh
set -e
cd "$(dirname "$0")/.."
PORT="${PORT:-8005}"
pkill -f "uvicorn sourceloop.main:app.*--port $PORT" 2>/dev/null || true
.venv/bin/alembic upgrade head
exec .venv/bin/uvicorn sourceloop.main:app --host 127.0.0.1 --port "$PORT"
