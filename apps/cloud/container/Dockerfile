FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv

FROM docker.io/library/python:3.13.15-slim-trixie@sha256:7e3a6aca9d74f93cca21a91d86a8dad8c34749afd5b4a98ee481c9c47b9f5ed4

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer-nogui \
    && rm -rf /var/lib/apt/lists/*

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PARSERIUM_TESSDATA_PATH=/app/tessdata

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev

RUN groupadd --gid 10001 parserium \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin parserium \
    && install -d -o 10001 -g 10001 -m 0755 /app/tessdata \
    && python -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/e12c65a915945e4c28e237a9b52bc4a8f39a0cec/eng.traineddata', '/app/tessdata/eng.traineddata')"

COPY --chown=10001:10001 apps/cloud/container/runner.py apps/cloud/container/server.py /app/container/
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "/app/container/server.py"]
