# syntax=docker/dockerfile:1.7

# sagewai-sandbox-base — minimal tool-runner image for development and tests.
# Plan 2 will add SHA-pinned FROM, SBOM attestation, and GHCR publish.

FROM python:3.12-slim-bookworm

# System utilities. Version pins added in Plan 2 after baseline is proven.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        jq \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Install the tool-runner from the monorepo source. Plan 2 switches to a
# PyPI install once sagewai-tool-runner is published.
WORKDIR /build
COPY packages/tool-runner /build/tool-runner
RUN pip install --no-cache-dir /build/tool-runner \
    && rm -rf /build

WORKDIR /workspace

# PID 1 is tini reaping a long-running idle process. Each tool call is a
# fresh `docker exec sagewai-tool-runner` that reads one JSON-RPC request
# from stdin and writes one response. This keeps per-run containers alive
# (shared /workspace across calls) without forcing a single long-lived
# attach stream on PID 1.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
