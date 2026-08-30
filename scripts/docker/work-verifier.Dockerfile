# syntax=docker/dockerfile:1.7

# Repository-specific, networkless verifier for the Sagewai platform smoke suite.
# The build script first produces this local base from the repository's pinned
# sandbox Dockerfile, then reports the immutable local image ID to the Work CLI.
ARG BASE_IMAGE=ghcr.io/sagewai/sandbox-base:dev
FROM ${BASE_IMAGE}

ARG TARGETARCH
ARG NODE_VERSION=20.20.2
ARG NODE_AMD64_SHA256=19e56f0825510207dd904f087fe52faa0a4eb6b2aab5f0ea7a33830d04888b8b
ARG NODE_ARM64_SHA256=47ef73d543ecf6eb19435f6c03a0ac4809b3bf0dd6b26c7c571efc2a6572a74d
ARG JUST_VERSION=1.57.0
ARG JUST_AMD64_SHA256=45b548094283cb9739af8f13273b8cddeee869f5b4ef2bb631b1f311cb566155
ARG JUST_ARM64_SHA256=f225044a81adea6e0b3a8b9370aaf374e6af76c8735ae263ac993df55fd137ec
ARG UV_VERSION=0.9.11
ARG UV_AMD64_SHA256=817c0722b437b4b45b9a7e0231616a09db76bab1b8d178ba7a9680c690db19f0
ARG UV_ARM64_SHA256=b695e1796449ea85f967b749f87283678ce284e2c042b4b6fa51fa36ec06f47c

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) NODE_ARCH="x64"; NODE_SHA="${NODE_AMD64_SHA256}" ;; \
      arm64) NODE_ARCH="arm64"; NODE_SHA="${NODE_ARM64_SHA256}" ;; \
      *) echo "unsupported architecture ${TARGETARCH}"; exit 1 ;; \
    esac; \
    archive="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.gz"; \
    curl -fsSL -o "/tmp/${archive}" "https://nodejs.org/dist/v${NODE_VERSION}/${archive}"; \
    echo "${NODE_SHA}  /tmp/${archive}" | sha256sum --check; \
    tar -xzf "/tmp/${archive}" -C /usr/local --strip-components=1; \
    rm "/tmp/${archive}"; \
    node --version

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) JUST_ARCH="x86_64"; JUST_SHA="${JUST_AMD64_SHA256}" ;; \
      arm64) JUST_ARCH="aarch64"; JUST_SHA="${JUST_ARM64_SHA256}" ;; \
      *) echo "unsupported architecture ${TARGETARCH}"; exit 1 ;; \
    esac; \
    archive="just-${JUST_VERSION}-${JUST_ARCH}-unknown-linux-musl.tar.gz"; \
    curl -fsSL -o "/tmp/${archive}" \
      "https://github.com/casey/just/releases/download/${JUST_VERSION}/${archive}"; \
    echo "${JUST_SHA}  /tmp/${archive}" | sha256sum --check; \
    tar -xzf "/tmp/${archive}" -C /usr/local/bin just; \
    rm "/tmp/${archive}"; \
    just --version

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) UV_ARCH="x86_64"; UV_SHA="${UV_AMD64_SHA256}" ;; \
      arm64) UV_ARCH="aarch64"; UV_SHA="${UV_ARM64_SHA256}" ;; \
      *) echo "unsupported architecture ${TARGETARCH}"; exit 1 ;; \
    esac; \
    archive="uv-${UV_ARCH}-unknown-linux-gnu.tar.gz"; \
    curl -fsSL -o "/tmp/${archive}" \
      "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${archive}"; \
    echo "${UV_SHA}  /tmp/${archive}" | sha256sum --check; \
    tar -xzf "/tmp/${archive}" -C /usr/local/bin --strip-components=1; \
    rm "/tmp/${archive}"; \
    uv --version

COPY pyproject.toml uv.lock /opt/sagewai-src/
COPY packages/sdk /opt/sagewai-src/packages/sdk
COPY packages/tool-runner /opt/sagewai-src/packages/tool-runner

RUN cd /opt/sagewai-src \
    && UV_PROJECT_ENVIRONMENT=/opt/sagewai-src/.venv \
       uv sync --frozen --package sagewai --group test --no-editable \
    && chmod -R a+rX /opt/sagewai-src/.venv

ENV PATH="/opt/sagewai-src/.venv:/opt/sagewai-src/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PYTHONPATH="/workspace/packages/sdk" \
    PYTHONDONTWRITEBYTECODE="1" \
    UV_PROJECT_ENVIRONMENT="/opt/sagewai-src/.venv" \
    UV_NO_SYNC="1" \
    UV_CACHE_DIR="/tmp/uv-cache" \
    HOME="/tmp"
