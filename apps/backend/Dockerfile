# syntax=docker/dockerfile:1.7
#
# Sagewai backend — thin production image that bundles the `sagewai` Python
# package with a minimal runtime and starts the FastAPI admin server.
#
# Published as ghcr.io/sagewai/backend:<version> on every v*.*.* tag by
# .github/workflows/release-backend.yml. Multi-arch (linux/amd64, linux/arm64).
#
# Build context expects a pre-built SDK wheel copied into ./dist/ by CI:
#   (from repo root) uv build --package sagewai --out-dir apps/backend/dist
#
# Local dev build:
#   docker build -t sagewai-backend:dev apps/backend
#
# Run:
#   docker run --rm -p 8000:8000 \
#     -e DATABASE_URL=postgres://... \
#     -e REDIS_URL=redis://... \
#     ghcr.io/sagewai/backend:latest

# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Expect a pre-built wheel in ./dist/. If missing, fall back to PyPI.
COPY dist/ /build/dist/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN if ls /build/dist/sagewai-*.whl >/dev/null 2>&1; then \
        pip install /build/dist/sagewai-*.whl; \
    else \
        pip install sagewai; \
    fi

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    SAGEWAI_LOG_LEVEL=INFO

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 sagewai \
    && useradd --system --uid 1001 --gid sagewai --home /home/sagewai --shell /usr/sbin/nologin sagewai \
    && mkdir -p /home/sagewai /var/lib/sagewai \
    && chown -R sagewai:sagewai /home/sagewai /var/lib/sagewai

COPY --from=builder /opt/venv /opt/venv
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER sagewai
WORKDIR /home/sagewai

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["sagewai", "admin", "serve", "--host", "0.0.0.0", "--port", "8000"]
