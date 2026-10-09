# syntax=docker/dockerfile:1.7
# Single image: the React UI is built and served by FastAPI (one origin, one port).

# ---------------------------------------------------------------- web build
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git: knowledge-base ingestion clones/refreshes the transcript repository.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./
COPY --from=web /web/dist ./static

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data/transcripts \
 && chown -R app:app /data /app \
 && chmod +x /app/docker-entrypoint.sh
USER app

ENV AGENT_CONFIG_DIR=/home/app/.lenny-assistant/claude-agent \
    STATIC_DIR=/app/static \
    TRANSCRIPTS_DIR=/data/transcripts

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
# Access logs come from our structured `http_request` event, so uvicorn's are disabled.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]

# ---------------------------------------------------------------- tests
# docker compose --profile test run --rm tests
FROM app AS test
COPY backend/requirements-dev.txt ./
USER root
RUN pip install -r requirements-dev.txt
USER app
ENTRYPOINT []
CMD ["python", "-m", "pytest", "-q"]
