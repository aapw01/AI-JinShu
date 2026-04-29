FROM node:20-bookworm-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        supervisor \
        python3 \
        python3-venv \
        python3-pip \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7.22 /uv /uvx /usr/local/bin/

FROM base AS backend-deps
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS web-deps
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM base AS builder
WORKDIR /app
COPY --from=backend-deps /app/.venv /app/.venv
COPY --from=web-deps /app/web/node_modules /app/web/node_modules
COPY app /app/app
COPY alembic /app/alembic
COPY presets /app/presets
COPY scripts /app/scripts
COPY pyproject.toml uv.lock alembic.ini /app/
COPY web /app/web

ENV PATH="/app/.venv/bin:/app/web/node_modules/.bin:${PATH}" \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_URL="" \
    API_TARGET_URL="http://127.0.0.1:8000"

RUN uv sync --frozen --no-dev
RUN cd /app/web && npm run build && npm prune --omit=dev

FROM base AS runtime
WORKDIR /app
RUN chown app:app /app

ENV PATH="/app/.venv/bin:/app/web/node_modules/.bin:${PATH}" \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

COPY --chown=app:app --from=builder /app/.venv /app/.venv
COPY --chown=app:app --from=builder /app/app /app/app
COPY --chown=app:app --from=builder /app/alembic /app/alembic
COPY --chown=app:app --from=builder /app/presets /app/presets
COPY --chown=app:app --from=builder /app/scripts /app/scripts
COPY --chown=app:app --from=builder /app/pyproject.toml /app/uv.lock /app/alembic.ini /app/
COPY --chown=app:app --from=builder /app/web/.next /app/web/.next
COPY --chown=app:app --from=builder /app/web/node_modules /app/web/node_modules
COPY --chown=app:app --from=builder /app/web/package.json /app/web/package-lock.json /app/web/next.config.js /app/web/
COPY --chown=app:app deploy/entrypoint.sh deploy/supervisord.conf /app/deploy/

EXPOSE 8000 3000

USER app

CMD ["bash", "/app/deploy/entrypoint.sh"]
