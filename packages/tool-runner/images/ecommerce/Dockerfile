# syntax=docker/dockerfile:1.7

# sagewai-sandbox-ecommerce — extends general with e-commerce CLI/SDK tooling.
#
# Reproducibility:
#   - Inherits all general guarantees (snapshot apt, pinned Python, hash-pinned pip)
#   - Stripe CLI: SHA-256-verified tarball (amd64 + arm64)
#   - Shopify CLI: npm SRI tarball verification (canonical pattern from general/Dockerfile)
#   - Salesforce SFDX CLI: npm SRI tarball verification
#   - BigCommerce Stencil CLI: npm SRI tarball verification
#   - pip extras: stripe, shopify-python-api, simple-salesforce (hash-pinned via ecommerce.txt)
#
# CLI decisions:
#   Shopify CLI (@shopify/cli 3.80.7)        — SHIP (npm SRI; last Node-18-compat version)
#   Stripe CLI (1.40.7)                      — SHIP (SHA-256 tarball)
#   Salesforce SFDX CLI (@salesforce/cli 2.131.7) — SHIP (npm SRI)
#   BigCommerce Stencil CLI (8.10.5)         — SHIP (npm SRI; last Node-18-compat version)
#   Magento CLI                              — DEFERRED to Plan 2.1 (see note below)
#
# Node.js version note:
#   The general base image ships Node.js 18.20.4 (Debian bookworm package).
#   Shopify CLI 3.80.7 is the last version that accepts ^18.20.0 || >=20.10.0.
#   Shopify CLI 3.81.0+ requires >=20.10.0 only.
#   Stencil CLI 8.10.5 is the last version with node >=18 (9.0.0+ requires >=20).
#   When the general image is upgraded to Node 20, bump these to latest.
#
# Magento CLI deferral:
#   The canonical Magento 2 CLI ships embedded inside the application itself
#   (bin/magento) and has no standalone download. Third-party Packagist packages
#   like magento/composer-magento-cli are either archived or require Magento Marketplace
#   authentication tokens (MAGE_ACCOUNT_PUBLIC_KEY / MAGE_ACCOUNT_PRIVATE_KEY) plus
#   a full PHP + Composer runtime (~200 MB). There is no amd64/arm64 prebuilt binary
#   or SRI-pinnable npm package. Deferred to Plan 2.1 for a PHP-enabled sub-variant.

ARG SANDBOX_GENERAL_TAG=dev
ARG SANDBOX_GENERAL_DIGEST
# hadolint ignore=DL3006
FROM ghcr.io/sagewai/sandbox-general:${SANDBOX_GENERAL_TAG}

ARG TARGETARCH

# ── Stripe CLI pins ──────────────────────────────────────────────────
ARG STRIPE_CLI_VERSION
ARG STRIPE_CLI_AMD64_SHA256
ARG STRIPE_CLI_ARM64_SHA256

# ── Shopify CLI pins (npm, SRI integrity) ────────────────────────────
ARG SHOPIFY_CLI_VERSION
ARG SHOPIFY_CLI_NPM_INTEGRITY

# ── Salesforce SFDX CLI pins (npm, SRI integrity) ────────────────────
ARG SFDX_VERSION
ARG SFDX_NPM_INTEGRITY

# ── BigCommerce Stencil CLI pins (npm, SRI integrity) ────────────────
ARG STENCIL_CLI_VERSION
ARG STENCIL_CLI_NPM_INTEGRITY

USER root

# ── 1. Stripe CLI (SHA-256 tarball — canonical pattern from base/Dockerfile gh block) ──
# https://github.com/stripe/stripe-cli/releases/download/v${STRIPE_CLI_VERSION}/stripe-linux-checksums.txt
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${STRIPE_CLI_AMD64_SHA256}"; STRIPE_ARCH="x86_64" ;; \
      arm64) SHA="${STRIPE_CLI_ARM64_SHA256}"; STRIPE_ARCH="arm64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/stripe.tar.gz \
       "https://github.com/stripe/stripe-cli/releases/download/v${STRIPE_CLI_VERSION}/stripe_${STRIPE_CLI_VERSION}_linux_${STRIPE_ARCH}.tar.gz"; \
    echo "${SHA}  /tmp/stripe.tar.gz" | sha256sum -c; \
    tar -xzf /tmp/stripe.tar.gz -C /usr/local/bin stripe; \
    chmod 0755 /usr/local/bin/stripe; \
    rm /tmp/stripe.tar.gz; \
    stripe version

# ── 2. Shopify CLI (npm SRI — canonical pattern from general/Dockerfile Claude Code block) ──
# https://www.npmjs.com/package/@shopify/cli
RUN set -eux; \
    TARBALL_URL="https://registry.npmjs.org/@shopify/cli/-/cli-${SHOPIFY_CLI_VERSION}.tgz"; \
    curl -fsSL -o /tmp/shopify-cli.tgz "${TARBALL_URL}"; \
    ACTUAL="sha512-$(openssl dgst -sha512 -binary /tmp/shopify-cli.tgz | base64 | tr -d '\n')"; \
    [ "${ACTUAL}" = "${SHOPIFY_CLI_NPM_INTEGRITY}" ] || { echo "Shopify CLI integrity mismatch"; echo "expected: ${SHOPIFY_CLI_NPM_INTEGRITY}"; echo "actual:   ${ACTUAL}"; exit 1; }; \
    npm install -g /tmp/shopify-cli.tgz; \
    rm /tmp/shopify-cli.tgz; \
    shopify version

# ── 3. Salesforce SFDX CLI (npm SRI) ────────────────────────────────
# https://www.npmjs.com/package/@salesforce/cli
RUN set -eux; \
    TARBALL_URL="https://registry.npmjs.org/@salesforce/cli/-/cli-${SFDX_VERSION}.tgz"; \
    curl -fsSL -o /tmp/sfdx.tgz "${TARBALL_URL}"; \
    ACTUAL="sha512-$(openssl dgst -sha512 -binary /tmp/sfdx.tgz | base64 | tr -d '\n')"; \
    [ "${ACTUAL}" = "${SFDX_NPM_INTEGRITY}" ] || { echo "SFDX CLI integrity mismatch"; echo "expected: ${SFDX_NPM_INTEGRITY}"; echo "actual:   ${ACTUAL}"; exit 1; }; \
    npm install -g /tmp/sfdx.tgz; \
    rm /tmp/sfdx.tgz; \
    sf version

# ── 4. BigCommerce Stencil CLI (npm SRI) ────────────────────────────
# https://www.npmjs.com/package/@bigcommerce/stencil-cli
# Note: --ignore-scripts is required because a transitive dependency triggers
# node-gyp (native addon build) which needs build-essential + python3.
# The native addon is an optional performance enhancement (fsevents / file-watching);
# Stencil CLI functions correctly without it in a container context.
RUN set -eux; \
    TARBALL_URL="https://registry.npmjs.org/@bigcommerce/stencil-cli/-/stencil-cli-${STENCIL_CLI_VERSION}.tgz"; \
    curl -fsSL -o /tmp/stencil-cli.tgz "${TARBALL_URL}"; \
    ACTUAL="sha512-$(openssl dgst -sha512 -binary /tmp/stencil-cli.tgz | base64 | tr -d '\n')"; \
    [ "${ACTUAL}" = "${STENCIL_CLI_NPM_INTEGRITY}" ] || { echo "Stencil CLI integrity mismatch"; echo "expected: ${STENCIL_CLI_NPM_INTEGRITY}"; echo "actual:   ${ACTUAL}"; exit 1; }; \
    npm install -g --ignore-scripts /tmp/stencil-cli.tgz; \
    rm /tmp/stencil-cli.tgz; \
    stencil --version

# ── 5. pip extras (ecommerce SDK libraries) ──────────────────────────
# Includes: stripe, shopify-python-api, simple-salesforce.
# All pinned with --require-hashes.
COPY packages/tool-runner/images/requirements/ecommerce.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt

WORKDIR /workspace
