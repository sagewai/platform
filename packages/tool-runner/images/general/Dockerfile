# syntax=docker/dockerfile:1.7

# sagewai-sandbox-general — extends base with cloud CLIs and scraping/AI tooling.
#
# Reproducibility:
#   - Inherits all base guarantees (snapshot apt, pinned Python, hash-pinned pip)
#   - AWS CLI v2: SHA-256-verified zip (amd64 + arm64)
#   - gcloud: SHA-256-verified tar.gz (x86_64 + arm)
#   - Azure CLI: pip-installed, pinned by version in general.txt --require-hashes
#     (Microsoft's native .deb packages are not SHA-pinnable per-file in CI)
#   - Claude Code: npm install with --integrity (SRI sha512 from registry)
#   - Codex CLI: npm install with --integrity (SRI sha512 from registry)
#     Both require Node.js ≥18; nodejs=18.20.4 from frozen Debian snapshot

# For :dev local builds pass SANDBOX_BASE_TAG=dev (no registry digest needed).
# For CI/release builds pass SANDBOX_BASE_DIGEST=sha256:... instead.
ARG SANDBOX_BASE_TAG=dev
ARG SANDBOX_BASE_DIGEST
# hadolint ignore=DL3006
FROM ghcr.io/sagewai/sandbox-base:${SANDBOX_BASE_TAG}

ARG TARGETARCH
ARG SNAPSHOT_DATE

# ── AWS CLI pins ─────────────────────────────────────────────────────
ARG AWS_CLI_VERSION
ARG AWS_CLI_AMD64_SHA256
ARG AWS_CLI_ARM64_SHA256

# ── gcloud CLI pins ──────────────────────────────────────────────────
ARG GCLOUD_VERSION
ARG GCLOUD_AMD64_SHA256
ARG GCLOUD_ARM64_SHA256

# ── Azure CLI version (installed via pip, pinned in general.txt) ─────
ARG AZ_CLI_VERSION

# ── Claude Code CLI pins (npm, SRI integrity) ────────────────────────
ARG CLAUDE_CODE_VERSION
ARG CLAUDE_CODE_NPM_INTEGRITY

# ── Codex CLI pins (npm, SRI integrity) ─────────────────────────────
ARG CODEX_VERSION
ARG CODEX_NPM_INTEGRITY

USER root

# ── 1. System packages: unzip (AWS CLI installer) + Node.js 18 ───────
# nodejs=18.20.4+dfsg-1~deb12u1 is present in bookworm main at SNAPSHOT_DATE.
# We need npm to install Claude Code and Codex, so we pull it here.
RUN apt-get -o Acquire::Check-Valid-Until=false update \
  && apt-get install -y --no-install-recommends \
       unzip \
       nodejs \
       npm \
  && rm -rf /var/lib/apt/lists/*

# ── 2. AWS CLI v2 ─────────────────────────────────────────────────────
# SHA-256-verified per arch. Tarball URL follows AWS naming convention.
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${AWS_CLI_AMD64_SHA256}"; ARCH="x86_64" ;; \
      arm64) SHA="${AWS_CLI_ARM64_SHA256}"; ARCH="aarch64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/awscli.zip \
        "https://awscli.amazonaws.com/awscli-exe-linux-${ARCH}-${AWS_CLI_VERSION}.zip"; \
    echo "${SHA}  /tmp/awscli.zip" | sha256sum -c; \
    unzip -q /tmp/awscli.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/awscli.zip /tmp/aws

# ── 3. gcloud CLI ────────────────────────────────────────────────────
# Google ships x86_64 and arm (32-bit); arm64 Linux images run the arm binary
# under kernel compat (standard for gcloud on aarch64 containers).
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${GCLOUD_AMD64_SHA256}"; GCLOUD_ARCH="x86_64" ;; \
      arm64) SHA="${GCLOUD_ARM64_SHA256}"; GCLOUD_ARCH="arm" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/gcloud.tar.gz \
        "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-${GCLOUD_VERSION}-linux-${GCLOUD_ARCH}.tar.gz"; \
    echo "${SHA}  /tmp/gcloud.tar.gz" | sha256sum -c; \
    tar -xzf /tmp/gcloud.tar.gz -C /opt; \
    /opt/google-cloud-sdk/install.sh --quiet --usage-reporting=false --path-update=false; \
    ln -sf /opt/google-cloud-sdk/bin/gcloud /usr/local/bin/gcloud; \
    ln -sf /opt/google-cloud-sdk/bin/gsutil /usr/local/bin/gsutil; \
    rm /tmp/gcloud.tar.gz

# ── 4. pip packages (includes Azure CLI via --require-hashes) ────────
# azure-cli is installed here alongside requests, httpx, beautifulsoup4,
# lxml, selectolax, and playwright. All pinned with --require-hashes.
# Using pip (not uv) inside the container so --require-hashes enforcement
# is consistent with the base image install approach.
COPY packages/tool-runner/images/requirements/general.txt /tmp/requirements-general.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-general.txt \
  && rm /tmp/requirements-general.txt

# ── 5. Playwright browser (chromium) ────────────────────────────────
RUN playwright install --with-deps chromium

# ── 6. Claude Code CLI (download tarball + SRI integrity verification) ──
# npm --integrity only applies to local paths, not registry packages. Instead:
# 1. Download the tarball from the npm registry
# 2. Verify SHA-512 matches the SRI integrity value recorded in pins.env
# 3. Install from the verified local tarball
RUN set -eux; \
    TARBALL_URL="https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-${CLAUDE_CODE_VERSION}.tgz"; \
    curl -fsSL -o /tmp/claude-code.tgz "${TARBALL_URL}"; \
    ACTUAL="sha512-$(openssl dgst -sha512 -binary /tmp/claude-code.tgz | base64 | tr -d '\n')"; \
    [ "${ACTUAL}" = "${CLAUDE_CODE_NPM_INTEGRITY}" ] || { echo "Claude Code integrity mismatch"; echo "expected: ${CLAUDE_CODE_NPM_INTEGRITY}"; echo "actual:   ${ACTUAL}"; exit 1; }; \
    npm install -g /tmp/claude-code.tgz; \
    rm /tmp/claude-code.tgz; \
    claude --version

# ── 7. Codex CLI (download tarball + SRI integrity verification) ─────
RUN set -eux; \
    TARBALL_URL="https://registry.npmjs.org/@openai/codex/-/codex-${CODEX_VERSION}.tgz"; \
    curl -fsSL -o /tmp/codex.tgz "${TARBALL_URL}"; \
    ACTUAL="sha512-$(openssl dgst -sha512 -binary /tmp/codex.tgz | base64 | tr -d '\n')"; \
    [ "${ACTUAL}" = "${CODEX_NPM_INTEGRITY}" ] || { echo "Codex integrity mismatch"; echo "expected: ${CODEX_NPM_INTEGRITY}"; echo "actual:   ${ACTUAL}"; exit 1; }; \
    npm install -g /tmp/codex.tgz; \
    rm /tmp/codex.tgz; \
    codex --version || true

WORKDIR /workspace
