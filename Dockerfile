FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
# exact runtime versions from the lock; then the project itself without re-resolving
RUN pip install --upgrade pip && pip install -r requirements.lock && pip install --no-deps .
COPY alembic.ini ./
COPY alembic ./alembic
COPY data ./data
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown -R app /data /app
USER app
ENV DATABASE_URL=sqlite+aiosqlite:////data/sourceloop.db APP_ENV=dev
EXPOSE 8000
# migrate, then serve. Seed explicitly: docker compose exec api python -m sourceloop.seed
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn sourceloop.main:app --host 0.0.0.0 --port 8000"]
