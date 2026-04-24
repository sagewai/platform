# syntax=docker/dockerfile:1.7

# sagewai-sandbox-api — extends general with API testing and design tooling.
#
# Reproducibility:
#   - Inherits all general guarantees (snapshot apt, pinned Python, hash-pinned pip)
#   - JRE + wrk: from frozen Debian snapshot (snapshot apt, bookworm)
#   - grpcurl: SHA-256-verified tarball (amd64 + arm64)
#   - k6: SHA-256-verified tarball (amd64 + arm64)
#   - Kong deck: SHA-256-verified tarball (amd64 + arm64)
#   - Apigee apigeecli: SHA-256-verified zip (amd64 only — no upstream arm64 Linux build)
#   - Newman: npm SRI tarball verification (canonical pattern from general/Dockerfile)
#   - AsyncAPI CLI: npm SRI tarball verification
#   - openapi-generator-cli: SHA-256-verified Maven JAR + wrapper script
#   - pip extras: httpie, openapi-spec-validator, jsonschema (hash-pinned via api.txt)
#
# CLI decisions:
#   Newman (6.2.2)                          — SHIP (npm SRI)
#   httpie (pip)                            — SHIP (pip --require-hashes)
#   openapi-generator-cli (7.14.0 JAR)      — SHIP (SHA-256 Maven JAR)
#   grpcurl (1.9.3)                         — SHIP (SHA-256 tarball)
#   k6 (1.7.1)                              — SHIP (SHA-256 tarball)
#   wrk                                     — SHIP (apt from Debian bookworm snapshot)
#   Kong deck (1.59.0)                      — SHIP (SHA-256 tarball)
#   Apigee apigeecli (2.17.0)               — SHIP amd64 only; arm64 skipped (no upstream build)
#   AsyncAPI CLI (6.0.0)                    — SHIP (npm SRI)
#
# wrk note: wg/wrk ships source-only on GitHub (no prebuilt binaries, no checksums file).
#   Debian bookworm's `wrk` package (2.1.0-4) is the canonical pinned installation path.
#   We freeze it via snapshot.debian.org at SNAPSHOT_DATE for reproducibility.
#
# apigeecli arm64 note: https://github.com/apigee/apigeecli/releases/tag/v2.17.0 only
#   ships apigeecli_v2.17.0_Linux_x86_64.zip; no arm64 Linux zip exists. On arm64
#   the RUN block is conditionally skipped with a log message.

ARG SANDBOX_GENERAL_TAG=dev
ARG SANDBOX_GENERAL_DIGEST
# hadolint ignore=DL3006
FROM ghcr.io/sagewai/sandbox-general:${SANDBOX_GENERAL_TAG}

ARG TARGETARCH
ARG SNAPSHOT_DATE

# ── grpcurl pins ─────────────────────────────────────────────────────
ARG GRPCURL_VERSION
ARG GRPCURL_AMD64_SHA256
ARG GRPCURL_ARM64_SHA256

# ── k6 pins ──────────────────────────────────────────────────────────
ARG K6_VERSION
ARG K6_AMD64_SHA256
ARG K6_ARM64_SHA256

# ── Kong deck pins ───────────────────────────────────────────────────
ARG DECK_VERSION
ARG DECK_AMD64_SHA256
ARG DECK_ARM64_SHA256

# ── Apigee apigeecli pins (amd64 only) ───────────────────────────────
ARG APIGEECLI_VERSION
ARG APIGEECLI_AMD64_SHA256

# ── Newman pins (npm, SRI integrity) ────────────────────────────────
ARG NEWMAN_VERSION
ARG NEWMAN_NPM_INTEGRITY

# ── AsyncAPI CLI pins (npm, SRI integrity) ───────────────────────────
ARG ASYNCAPI_CLI_VERSION
ARG ASYNCAPI_CLI_NPM_INTEGRITY

# ── openapi-generator-cli jar pins ──────────────────────────────────
ARG OPENAPI_GENERATOR_VERSION
ARG OPENAPI_GENERATOR_JAR_SHA256

USER root

# ── 1. JRE (for openapi-generator) + wrk (from Debian snapshot apt) ──
# default-jre-headless satisfies Java 17 on bookworm. wrk 2.1.0-4 is in
# bookworm main at SNAPSHOT_DATE. Both installed together to share one apt layer.
RUN echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${SNAPSHOT_DATE}/ bookworm main" \
      > /etc/apt/sources.list.d/snapshot.list \
  && apt-get -o Acquire::Check-Valid-Until=false update \
  && apt-get install -y --no-install-recommends \
       default-jre-headless \
       wrk \
  && rm -rf /var/lib/apt/lists/*

# ── 2. grpcurl (SHA-256 tarball — canonical pattern from base/Dockerfile gh block) ──
# https://github.com/fullstorydev/grpcurl/releases/tag/v${GRPCURL_VERSION}
# (no checksums.txt in this release; SHA-256 computed directly from downloaded tarballs)
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${GRPCURL_AMD64_SHA256}"; GRPC_ARCH="x86_64" ;; \
      arm64) SHA="${GRPCURL_ARM64_SHA256}"; GRPC_ARCH="arm64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/grpcurl.tar.gz \
       "https://github.com/fullstorydev/grpcurl/releases/download/v${GRPCURL_VERSION}/grpcurl_${GRPCURL_VERSION}_linux_${GRPC_ARCH}.tar.gz"; \
    echo "${SHA}  /tmp/grpcurl.tar.gz" | sha256sum -c; \
    tar -xzf /tmp/grpcurl.tar.gz -C /usr/local/bin grpcurl; \
    chmod 0755 /usr/local/bin/grpcurl; \
    rm /tmp/grpcurl.tar.gz; \
    grpcurl --version

# ── 3. k6 (SHA-256 tarball) ──────────────────────────────────────────
# https://github.com/grafana/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-checksums.txt
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${K6_AMD64_SHA256}"; K6_ARCH="amd64" ;; \
      arm64) SHA="${K6_ARM64_SHA256}"; K6_ARCH="arm64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/k6.tar.gz \
       "https://github.com/grafana/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-linux-${K6_ARCH}.tar.gz"; \
    echo "${SHA}  /tmp/k6.tar.gz" | sha256sum -c; \
    tar -xzf /tmp/k6.tar.gz --strip-components=1 -C /usr/local/bin "k6-v${K6_VERSION}-linux-${K6_ARCH}/k6"; \
    chmod 0755 /usr/local/bin/k6; \
    rm /tmp/k6.tar.gz; \
    k6 version

# ── 4. Kong deck (SHA-256 tarball) ───────────────────────────────────
# https://github.com/Kong/deck/releases/download/v${DECK_VERSION}/checksums.txt
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${DECK_AMD64_SHA256}"; DECK_ARCH="amd64" ;; \
      arm64) SHA="${DECK_ARM64_SHA256}"; DECK_ARCH="arm64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/deck.tar.gz \
       "https://github.com/Kong/deck/releases/download/v${DECK_VERSION}/deck_${DECK_VERSION}_linux_${DECK_ARCH}.tar.gz"; \
    echo "${SHA}  /tmp/deck.tar.gz" | sha256sum -c; \
    tar -xzf /tmp/deck.tar.gz -C /usr/local/bin deck; \
    chmod 0755 /usr/local/bin/deck; \
    rm /tmp/deck.tar.gz; \
    deck version

# ── 5. Apigee apigeecli (SHA-256 zip — amd64 only) ───────────────────
# https://github.com/apigee/apigeecli/releases/tag/v${APIGEECLI_VERSION}
# arm64 Linux: no upstream release binary — skipped with informational message.
# Upstream only ships: Darwin arm64, Darwin x86_64, Linux x86_64, Windows x86_64.
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${APIGEECLI_AMD64_SHA256}"; \
             curl -fsSL -o /tmp/apigeecli.zip \
               "https://github.com/apigee/apigeecli/releases/download/v${APIGEECLI_VERSION}/apigeecli_v${APIGEECLI_VERSION}_Linux_x86_64.zip"; \
             echo "${SHA}  /tmp/apigeecli.zip" | sha256sum -c; \
             unzip -q /tmp/apigeecli.zip "apigeecli_v${APIGEECLI_VERSION}_Linux_x86_64/apigeecli" -d /tmp/apigeecli_extract; \
             install -m 0755 "/tmp/apigeecli_extract/apigeecli_v${APIGEECLI_VERSION}_Linux_x86_64/apigeecli" /usr/local/bin/apigeecli; \
             rm -rf /tmp/apigeecli.zip /tmp/apigeecli_extract; \
             apigeecli --version ;; \
      arm64) echo "INFO: apigeecli v${APIGEECLI_VERSION} has no arm64 Linux build; skipping." ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac

# ── 6. Newman (npm SRI — canonical pattern from general/Dockerfile Claude Code block) ──
# https://www.npmjs.com/package/newman
RUN set -eux; \
    TARBALL_URL="https://registry.npmjs.org/newman/-/newman-${NEWMAN_VERSION}.tgz"; \
    curl -fsSL -o /tmp/newman.tgz "${TARBALL_URL}"; \
    ACTUAL="sha512-$(openssl dgst -sha512 -binary /tmp/newman.tgz | base64 | tr -d '\n')"; \
    [ "${ACTUAL}" = "${NEWMAN_NPM_INTEGRITY}" ] || { echo "Newman integrity mismatch"; echo "expected: ${NEWMAN_NPM_INTEGRITY}"; echo "actual:   ${ACTUAL}"; exit 1; }; \
    npm install -g /tmp/newman.tgz; \
    rm /tmp/newman.tgz; \
    newman --version

# ── 7. AsyncAPI CLI (npm SRI) ────────────────────────────────────────
# https://www.npmjs.com/package/@asyncapi/cli
RUN set -eux; \
    TARBALL_URL="https://registry.npmjs.org/@asyncapi/cli/-/cli-${ASYNCAPI_CLI_VERSION}.tgz"; \
    curl -fsSL -o /tmp/asyncapi-cli.tgz "${TARBALL_URL}"; \
    ACTUAL="sha512-$(openssl dgst -sha512 -binary /tmp/asyncapi-cli.tgz | base64 | tr -d '\n')"; \
    [ "${ACTUAL}" = "${ASYNCAPI_CLI_NPM_INTEGRITY}" ] || { echo "AsyncAPI CLI integrity mismatch"; echo "expected: ${ASYNCAPI_CLI_NPM_INTEGRITY}"; echo "actual:   ${ACTUAL}"; exit 1; }; \
    npm install -g /tmp/asyncapi-cli.tgz; \
    rm /tmp/asyncapi-cli.tgz; \
    asyncapi --version

# ── 8. openapi-generator-cli (SHA-256-verified Maven JAR + wrapper script) ──
# https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/${OPENAPI_GENERATOR_VERSION}/
# SHA-256 computed from the downloaded jar (Maven only provides .sha1 and .md5 files;
# we verify with sha256sum directly).
RUN set -eux; \
    curl -fsSL -o /usr/local/lib/openapi-generator-cli.jar \
       "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/${OPENAPI_GENERATOR_VERSION}/openapi-generator-cli-${OPENAPI_GENERATOR_VERSION}.jar"; \
    echo "${OPENAPI_GENERATOR_JAR_SHA256}  /usr/local/lib/openapi-generator-cli.jar" | sha256sum -c; \
    printf '#!/bin/sh\nexec java -jar /usr/local/lib/openapi-generator-cli.jar "$@"\n' \
       > /usr/local/bin/openapi-generator; \
    chmod 0755 /usr/local/bin/openapi-generator; \
    openapi-generator version

# ── 9. pip extras (API SDK libraries) ────────────────────────────────
# Includes: httpie, openapi-spec-validator, jsonschema.
# All pinned with --require-hashes.
COPY packages/tool-runner/images/requirements/api.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt

WORKDIR /workspace
