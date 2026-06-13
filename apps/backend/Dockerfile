# syntax=docker/dockerfile:1.7
#
# Sagewai backend — thin production image that builds the `sagewai` Python
# package from source and starts the FastAPI admin server.
#
# Published as ghcr.io/sagewai/backend:<version> on every v*.*.* tag by
# .github/workflows/release-backend.yml. Multi-arch (linux/amd64, linux/arm64).
#
# The build context is the REPO ROOT (so packages/sdk is visible). Build it via
# the compose `build:` section or directly:
#   docker build -t sagewai-backend:dev -f apps/backend/Dockerfile .
# `just stack-up` / `docker compose up -d --build` do this for you. No prebuilt
# wheel and no PyPI fallback — the image runs exactly the checked-out source.
#
# Run:
#   docker run --rm -p 8000:8000 \
#     -e DATABASE_URL=postgresql://... \
#     -e REDIS_URL=redis://... \
#     ghcr.io/sagewai/backend:latest

# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.13.13-slim@sha256:aa938a849bcb82dce8f49480f056ab82bf5c1c3ebc294f0430f37b6820e7f286 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install in two layers so editing the source doesn't reinstall every
# dependency — the difference between a multi-minute rebuild and a few seconds:
#
#   1. Dependencies — keyed on the manifest (pyproject.toml + README.md) ONLY.
#      We copy the manifest plus an empty `sagewai/__init__.py` stub so
#      hatchling can build metadata and pip can resolve + install the full
#      dependency tree, then drop the stub package itself. Docker reuses this
#      layer's cache on every build until the dependency list actually changes.
#   2. Source — copy the real packages/sdk, then a `--no-deps` install of the
#      package. A code change invalidates only this layer, so the rebuild just
#      recompiles the (pure-Python) sagewai wheel and installs it in seconds;
#      the multi-minute dependency install above is untouched.
#
# The build context is the repo root, so packages/sdk is the actual checkout —
# no prebuilt wheel, no PyPI fallback; the image runs exactly the code you
# cloned. The BuildKit cache mount persists pip's download/wheel cache, so even
# a dependency change reinstalls from cache instead of re-downloading.
COPY packages/sdk/pyproject.toml packages/sdk/README.md /build/sdk/
RUN mkdir -p /build/sdk/sagewai && touch /build/sdk/sagewai/__init__.py
RUN --mount=type=cache,target=/root/.cache/pip \
        pip install "/build/sdk[fastapi,postgres]" \
    && pip uninstall -y sagewai

COPY packages/sdk /build/sdk
RUN --mount=type=cache,target=/root/.cache/pip \
        pip install --no-deps "/build/sdk[fastapi,postgres]"

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.13.13-slim@sha256:aa938a849bcb82dce8f49480f056ab82bf5c1c3ebc294f0430f37b6820e7f286 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    SAGEWAI_LOG_LEVEL=INFO \
    SAGEWAI_RUNTIME=container

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 sagewai \
    && useradd --system --uid 1001 --gid sagewai --home /home/sagewai --shell /usr/sbin/nologin sagewai \
    && mkdir -p /home/sagewai /var/lib/sagewai \
    && chown -R sagewai:sagewai /home/sagewai /var/lib/sagewai

COPY --from=builder /opt/venv /opt/venv
COPY apps/backend/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER sagewai
WORKDIR /home/sagewai

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/summary >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["sagewai", "admin", "serve", "--host", "0.0.0.0", "--port", "8000"]
