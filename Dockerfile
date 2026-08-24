FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv

FROM docker.io/library/python:3.13.15-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app

FROM python-base AS backend-test
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --group dev --no-install-project
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY backend/scripts ./scripts
COPY backend/src ./src
COPY backend/tests ./tests
RUN uv sync --frozen --group dev
RUN .venv/bin/ruff check src tests
RUN .venv/bin/mypy src
RUN .venv/bin/pytest -q tests/unit

FROM docker.io/library/node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS web-base
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY contracts/openapi/dashboard-v1.json /contracts/openapi/dashboard-v1.json
COPY apps/web/ ./
RUN npm run contract:generate

FROM web-base AS web-test
RUN npm test
RUN npm run build

FROM mcr.microsoft.com/playwright:v1.62.1-noble@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e AS browser-test
WORKDIR /web
COPY --from=web-base /web /web
CMD ["npm", "run", "test:e2e"]

FROM web-base AS web-build
RUN npm run build

FROM python-base AS python-build
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev

FROM docker.io/library/python:3.13.15-slim-trixie@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS runtime
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
RUN groupadd --gid 10001 collector \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin collector
WORKDIR /app
COPY --from=python-build --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 backend/src /app/src
COPY --chown=10001:10001 backend/alembic.ini /app/alembic.ini
COPY --chown=10001:10001 backend/migrations /app/migrations
COPY --from=web-build --chown=10001:10001 /web/dist /app/static
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "parserium_collector.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
