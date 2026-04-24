# syntax=docker/dockerfile:1.7

# sagewai-sandbox-erp — extends general with ERP integration tooling.
#
# Reproducibility:
#   - Inherits all general guarantees (snapshot apt, pinned Python, hash-pinned pip)
#   - unixodbc-dev from frozen Debian snapshot (required by pyodbc)
#   - pip extras: odoo-client-lib, requests-oauthlib, simple-salesforce, pyodbc
#     (hash-pinned via erp.txt)
#
# CLI decisions — all 4 candidates deferred to Plan 2.1:
#
#   SAP BTP CLI (btp) — DEFERRED
#     https://tools.hana.ondemand.com/additional/btp-cli-linux-<arch>-2.106.1.tar.gz
#     Download redirects to EULA acceptance page (HTTP 302 → SAP Developer License 3.2).
#     Hardcoding EULA acceptance in an automated Docker build is legally inappropriate.
#
#   Oracle NetSuite SuiteCloud CLI (@oracle/suitecloud-cli 3.1.2) — DEFERRED
#     npm postinstall downloads a ~40 MB JAR from system.netsuite.com at build time
#     (not SHA-pinnable here; upstream URL: https://system.netsuite.com/download/
#     suitecloud-sdk/25.2/cli-2025.2.1.jar). Also requires a Java runtime to execute.
#     Both the live download and the JVM dependency make this impractical.
#     The Oracle FUTC license acceptance flag (npm_config_acceptsuitecloudsdklicense)
#     is intentional CI plumbing, but the JAR issue blocks reliable pinning.
#
#   Microsoft Dynamics 365 PAC CLI (pac) — DEFERRED
#     Distributed as Microsoft.PowerApps.CLI.Tool (NuGet dotnet tool, v2.6.4).
#     Only MSIL assemblies; no self-contained Linux binary exists in the nupkg.
#     Requires a .NET 10 runtime install (~200 MB); adds excessive image weight.
#
#   Zoho CLI (zcli) — DEFERRED
#     zcli.zoho.com is unreachable; no npm package found; no public GitHub release
#     with Linux (amd64 + arm64) binaries and SHA checksums.

# For :dev local builds pass SANDBOX_GENERAL_TAG=dev (no registry digest needed).
# For CI/release builds pass SANDBOX_GENERAL_DIGEST=sha256:... instead.
ARG SANDBOX_GENERAL_TAG=dev
ARG SANDBOX_GENERAL_DIGEST
# hadolint ignore=DL3006
FROM ghcr.io/sagewai/sandbox-general:${SANDBOX_GENERAL_TAG}

ARG TARGETARCH
ARG SNAPSHOT_DATE

USER root

# ── 1. Build-time deps from snapshot (unixodbc-dev for pyodbc) ───────
# snapshot.list was set in the general stage. We add a fresh entry here
# so the sources file reflects the same SNAPSHOT_DATE even if layers differ.
RUN echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${SNAPSHOT_DATE}/ bookworm main" \
      > /etc/apt/sources.list.d/snapshot.list \
  && apt-get -o Acquire::Check-Valid-Until=false update \
  && apt-get install -y --no-install-recommends unixodbc-dev \
  && rm -rf /var/lib/apt/lists/*

# ── 2. pip extras (ERP SDK libraries) ────────────────────────────────
# Includes: odoo-client-lib, requests-oauthlib, simple-salesforce, pyodbc.
# pyodbc links against libodbc which is satisfied by unixodbc-dev installed above.
COPY packages/tool-runner/images/requirements/erp.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt

WORKDIR /workspace
