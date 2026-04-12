# Sagewai platform — task runner.
#
# Dispatches to per-package tools (uv, pnpm). Contributors never need to
# cd into individual packages — everything is reachable from here.
#
# Quick start:
#   just bootstrap      # first-time setup (uv + pnpm + hooks)
#   just smoke           # fast 29-test sanity check
#   just dev-all         # run backend + admin UI concurrently

set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Show all available recipes
default:
    @just --list

# ── Setup ──────────────────────────────────────────────────────────────────

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

# Remove build artifacts
clean:
    rm -rf packages/sdk/dist packages/sdk/build .pytest_cache .mypy_cache .ruff_cache
    rm -rf apps/admin/.next apps/docs/.next apps/vscode-extension/out apps/backend/dist
    find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true

# Check all required development tools are installed
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

# ── Testing ────────────────────────────────────────────────────────────────

# Run full SDK test suite (~2904 tests)
test: sdk-test

# Fast 29-test smoke pass (no LLM, no services)
smoke: sdk-smoke

# Performance micro-benchmarks with fixed time budgets
perf: sdk-perf

# ── Build ──────────────────────────────────────────────────────────────────

# Build every package for release
build: sdk-build admin-build docs-build vscode-build

# Lint every package
lint: sdk-lint admin-lint docs-lint vscode-lint

# Format all code (Python only for now)
format: sdk-format

# Type-check all packages (Python only for now)
typecheck: sdk-typecheck

# ── SDK (Python) ───────────────────────────────────────────────────────────

# Run SDK unit tests (full suite, ~14s)
sdk-test:
    uv run --package sagewai pytest packages/sdk/tests/ -m "not integration and not perf" -o "addopts="

# Run SDK smoke tests only (29 tests, ~1s)
sdk-smoke:
    uv run --package sagewai pytest packages/sdk/tests/test_smoke.py -v -o "addopts="

# Run SDK performance micro-benchmarks
sdk-perf:
    uv run --package sagewai pytest packages/sdk/tests/test_perf.py -v -m perf -o "addopts="

# Build SDK wheel + sdist
sdk-build:
    rm -rf packages/sdk/dist
    uv build --package sagewai --out-dir packages/sdk/dist

# Lint SDK with ruff
sdk-lint:
    uv run --with ruff ruff check packages/sdk/sagewai/

# Format SDK with ruff
sdk-format:
    uv run --with ruff ruff format packages/sdk/sagewai/ packages/sdk/tests/

# Type-check SDK with mypy
sdk-typecheck:
    uv run --with mypy mypy packages/sdk/sagewai/

# ── Admin UI (Next.js) ─────────────────────────────────────────────────────

# Start admin dev server on :3008
admin-dev:
    pnpm --filter @sagewai/admin dev

# Build admin production bundle
admin-build:
    pnpm --filter @sagewai/admin build

# Lint admin
admin-lint:
    pnpm --filter @sagewai/admin lint

# ── Docs (Next.js + Cloudflare) ────────────────────────────────────────────

# Start docs dev server on :3010
docs-dev:
    pnpm --filter @sagewai/docs dev

# Build docs production bundle (static export)
docs-build:
    pnpm --filter @sagewai/docs build

# Lint docs
docs-lint:
    pnpm --filter @sagewai/docs lint

# ── VS Code Extension ─────────────────────────────────────────────────────

# Build VS Code extension
vscode-build:
    pnpm --filter sagewai build 2>/dev/null || true

# Lint VS Code extension
vscode-lint:
    pnpm --filter sagewai lint 2>/dev/null || true

# ── Backend Docker ─────────────────────────────────────────────────────────

# Build backend Docker image locally
backend-build:
    uv build --package sagewai --out-dir apps/backend/dist
    cp LICENSE COMMERCIAL-LICENSE.md apps/backend/
    docker build -t sagewai-backend:dev apps/backend

# ── Dev Orchestration ──────────────────────────────────────────────────────

# Run backend (FastAPI :8000) + admin UI (:3008) concurrently
dev-all:
    #!/usr/bin/env bash
    trap 'kill 0' EXIT
    echo "Starting backend on :8000 and admin on :3008..."
    uv run --package sagewai sagewai admin serve --host 0.0.0.0 --port 8000 &
    pnpm --filter @sagewai/admin dev &
    wait

# Start backend + admin via Docker
admin-up:
    ./scripts/admin-up.sh

# Start full stack via Docker Compose (postgres + redis + backend + admin)
compose-up:
    docker compose up -d

# Stop and remove Docker Compose stack
compose-down:
    docker compose down

# ── Diagnostics ────────────────────────────────────────────────────────────

# Check installation health
doctor:
    uv run --package sagewai sagewai doctor

# Check infrastructure connectivity
status:
    uv run --package sagewai sagewai status
