# Sagewai platform — task runner.
#
# Dispatches to per-package tools (uv, pnpm). Contributors never need to
# cd into individual packages — everything is reachable from here.
#
# Recipes are grouped by lifecycle:
#   bootstrap → dev → test → smoke → perf → build → deploy
#
# Quick start:
#   just bootstrap      # first-time setup (uv + pnpm + hooks)
#   just smoke           # fast 35-test sanity check
#   just dev-all         # run backend + admin UI concurrently

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Show all available recipes
default:
    @just --list

# ── Bootstrap ──────────────────────────────────────────────────────────────
# First-time setup, dependency sync, prereq checks, build-artifact cleanup.

# First-time setup (install uv, pnpm, just, hooks, deps)
bootstrap:
    @./scripts/bootstrap.sh

# Sync all deps (uv workspace + pnpm workspace)
install:
    uv sync --all-packages
    pnpm -r install

# Sync all deps including dev/test groups
install-all:
    uv sync --all-packages --group test
    pnpm -r install

# Verify required dev tools are installed (python3, uv, node, pnpm, just, docker)
prereqs:
    #!/usr/bin/env bash
    set -e
    OK="\033[32m✓\033[0m"; FAIL="\033[31m✗\033[0m"; MISSING=""
    check() {
      if command -v "$1" &>/dev/null; then
        echo -e "$OK $1 — $("$1" --version 2>&1 | head -1)"
      else
        echo -e "$FAIL $1 — not found"
        MISSING=1
      fi
    }
    echo "Checking prerequisites..."
    echo ""
    check python3
    check uv
    check node
    check pnpm
    check just
    check docker
    echo ""
    if [ -n "$MISSING" ]; then
      echo -e "\033[31mInstall missing tools. See DEVELOPMENT.md\033[0m"
      exit 1
    fi
    echo -e "\033[32mAll prerequisites met.\033[0m"

# Remove SDK + admin + docs + vscode + backend build artifacts and pyc/cache dirs
clean:
    rm -rf packages/sdk/dist packages/sdk/build .pytest_cache .mypy_cache .ruff_cache
    rm -rf apps/admin/.next apps/docs/.next apps/vscode-extension/out apps/backend/dist
    find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true

# ── Dev ────────────────────────────────────────────────────────────────────
# Long-running local development — dev servers, full stacks, diagnostics.

# Run backend (FastAPI :8000) + admin UI (:3008) concurrently
dev-all:
    #!/usr/bin/env bash
    trap 'kill 0' EXIT
    echo "Starting backend on :8000 and admin on :3008..."
    uv run --package sagewai sagewai admin serve --host 0.0.0.0 --port 8000 &
    pnpm --filter @sagewai/admin dev &
    wait

# Start admin Next.js dev server on :3008
admin-dev:
    pnpm --filter @sagewai/admin dev

# Start backend + admin via Docker (scripts/admin-up.sh)
admin-up:
    ./scripts/admin-up.sh

# Start docs Next.js dev server on :3010
docs-dev:
    pnpm --filter @sagewai/docs dev

# Start full stack via Docker Compose (postgres + redis + backend + admin)
compose-up:
    docker compose up -d

# Stop and remove the Docker Compose stack
compose-down:
    docker compose down

# Run sagewai SDK installation health check
doctor:
    uv run --package sagewai sagewai doctor

# Run sagewai infrastructure connectivity check
status:
    uv run --package sagewai sagewai status

# ── Test ───────────────────────────────────────────────────────────────────
# Full unit suite, e2e suite, lint / format / typecheck (CI hygiene).

# Run full SDK test suite (~3900 tests, ~17s) — alias for sdk-test
test: sdk-test

# Run SDK unit tests (full suite, ~17s) directly via pytest
sdk-test:
    uv run --package sagewai pytest packages/sdk/tests/ -m "not integration and not perf" -o "addopts="

# Run admin Playwright e2e tests (auto-starts backend + frontend)
admin-e2e:
    pnpm --filter @sagewai/admin test:e2e

# Run admin Playwright e2e tests in interactive UI mode
admin-e2e-ui:
    pnpm --filter @sagewai/admin test:e2e:ui

# Lint every package (Python ruff + TS eslint across admin, docs, vscode)
lint: sdk-lint admin-lint docs-lint vscode-lint

# Lint SDK with ruff
sdk-lint:
    uv run --with ruff ruff check packages/sdk/sagewai/

# Lint admin Next.js app
admin-lint:
    pnpm --filter @sagewai/admin lint

# Lint docs Next.js app
docs-lint:
    pnpm --filter @sagewai/docs lint

# Lint the VS Code extension
vscode-lint:
    pnpm --filter sagewai lint 2>/dev/null || true

# Format all code (Python ruff format — TS formatters not wired here yet)
format: sdk-format

# Format SDK Python code with ruff
sdk-format:
    uv run --with ruff ruff format packages/sdk/sagewai/ packages/sdk/tests/

# Type-check all packages (Python mypy — TS typecheck happens in admin-build)
typecheck: sdk-typecheck

# Type-check SDK with mypy
sdk-typecheck:
    uv run --with mypy mypy packages/sdk/sagewai/

# ── Smoke ──────────────────────────────────────────────────────────────────
# Sub-second sanity checks — no LLM calls, no services, safe to run anywhere.

# Fast 35-test smoke pass (no LLM, no services) — alias for sdk-smoke
smoke: sdk-smoke

# Run SDK smoke tests directly (35 tests, ~0.1s)
sdk-smoke:
    uv run --package sagewai pytest packages/sdk/tests/test_smoke.py -v -o "addopts="

# ── Perf ───────────────────────────────────────────────────────────────────
# Performance micro-benchmarks with fixed time budgets that fail CI on regression.

# Run all perf micro-benchmarks — alias for sdk-perf
perf: sdk-perf

# Run SDK performance micro-benchmarks
sdk-perf:
    uv run --package sagewai pytest packages/sdk/tests/test_perf.py -v -m perf -o "addopts="

# ── Build ──────────────────────────────────────────────────────────────────
# Produce release artefacts — wheels, Next.js bundles, Docker images.

# Build every package for release (sdk wheel + admin + docs + vscode)
build: sdk-build admin-build docs-build vscode-build

# Build SDK wheel + sdist into packages/sdk/dist
sdk-build:
    rm -rf packages/sdk/dist
    uv build --package sagewai --out-dir packages/sdk/dist

# Build admin Next.js production bundle
admin-build:
    pnpm --filter @sagewai/admin build

# Build docs Next.js production bundle (static export)
docs-build:
    pnpm --filter @sagewai/docs build

# Build the VS Code extension (best-effort; tolerates missing tsc)
vscode-build:
    pnpm --filter sagewai build 2>/dev/null || true

# Build the backend Docker image locally (sagewai-backend:dev)
backend-build:
    uv build --package sagewai --out-dir apps/backend/dist
    cp LICENSE COMMERCIAL-LICENSE.md apps/backend/
    docker build -t sagewai-backend:dev apps/backend

# ── Deploy ─────────────────────────────────────────────────────────────────
# No deploy recipes live here. Production deploys are externalised:
#
#   • SDK         → release-sdk.yml on tag push (publishes to PyPI)
#   • admin/backend GHCR → release-admin.yml / release-backend.yml on tag push
#   • docs.sagewai.ai    → Cloudflare Workers Builds polls main and rebuilds
#   • VS Code Marketplace → release-vscode.yml on tag push
#
# To cut a release: `pnpm changeset && ./scripts/release.sh && git push --follow-tags`.
# See README.md → "Versioning and release" for the full flow.
