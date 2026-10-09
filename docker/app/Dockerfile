# CohortSplit application image: FastAPI backend serving the built React frontend.
# Build context: repository root.

FROM node:22.12.0-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.12.8-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    COHORTSPLIT_FRONTEND_DIST=/app/frontend-dist
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
RUN uv sync --frozen --no-dev
COPY --from=frontend /build/dist ./frontend-dist

RUN useradd --system --uid 10001 --no-create-home cohortsplit
USER cohortsplit

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=10 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=2).status == 200 else 1)"]
# Apply migrations, then serve. Exits non-zero (fail closed) if settings are invalid.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn cohortsplit.app:create_app --factory --host 0.0.0.0 --port 8000 --no-server-header"]
