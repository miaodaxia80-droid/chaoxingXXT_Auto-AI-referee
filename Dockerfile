FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@11.16.0 --activate
RUN pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm build

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/chaoxing \
    CX_ENVIRONMENT=production \
    CX_DATA_DIR=/app/data \
    CX_DATABASE_URL=sqlite:////app/data/chaoxing.db \
    CX_FRONTEND_DIR=/app/frontend \
    PATH="/opt/chaoxing/bin:$PATH"

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock /build/backend/
COPY backend/src /build/backend/src
RUN cd /build/backend && uv sync --locked --no-dev --no-editable

COPY backend/alembic.ini /app/alembic.ini
COPY backend/alembic /app/alembic

COPY --from=frontend-build /build/.frontend-dist /app/frontend

VOLUME /app/data
EXPOSE 5002

CMD ["sh", "-c", "python -m alembic upgrade head && exec chaoxing-app serve --host 0.0.0.0 --port 5002"]
