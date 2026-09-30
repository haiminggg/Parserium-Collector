FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv

FROM docker.io/library/golang:1.24.8-bookworm@sha256:4ed690d6649d63c312b99a6120025ec79ce3b542968a37da53d6236c7c61a848 AS minio-build
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
RUN CGO_ENABLED=0 go install github.com/minio/minio@${MINIO_COMMIT}

FROM docker.io/library/debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS minio-verification
RUN groupadd --gid 10002 minio \
    && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin minio \
    && install -d -o 10002 -g 10002 -m 0700 /data /minio-certs
COPY --from=minio-build /go/bin/minio /usr/local/bin/minio
COPY --chmod=0555 deploy/minio-entrypoint.sh /usr/local/bin/minio-entrypoint
USER 10002:10002
ENTRYPOINT ["/usr/local/bin/minio-entrypoint"]

FROM minio-verification AS minio-hosted-local

FROM docker.io/library/python:3.13.15-slim-trixie@sha256:7e3a6aca9d74f93cca21a91d86a8dad8c34749afd5b4a98ee481c9c47b9f5ed4 AS python-runtime-base

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer-nogui \
    && rm -rf /var/lib/apt/lists/*

FROM python-runtime-base AS python-base

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
RUN .venv/bin/ruff format --check src tests
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

FROM python-runtime-base AS runtime
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
COPY --chmod=0555 deploy/container-entrypoint.sh /usr/local/bin/parserium-entrypoint
USER 10001:10001
RUN python -c "import parserium_collector.features.storage.factory; import parserium_collector.features.storage.s3"
ENTRYPOINT ["/usr/local/bin/parserium-entrypoint"]
EXPOSE 8080
CMD ["uvicorn", "parserium_collector.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
