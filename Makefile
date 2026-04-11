# Sagewai platform — umbrella Makefile.
#
# Dispatches to per-package Makefiles / pnpm scripts. Contributors never
# need to cd into individual packages — everything is reachable from here.
#
# Common entry points:
#   make bootstrap      # first-time setup (uv + pnpm + hooks)
#   make install        # sync all deps (uv + pnpm)
#   make test           # run all tests (sdk pytest + per-app checks)
#   make build          # build sdk wheel + admin/docs/vscode prod builds
#   make dev-all        # run backend + admin UI concurrently
#   make admin-up       # one-command docker stack (postgres+redis+backend+admin)
#   make compose-up     # equivalent via root docker-compose.yml
#   make clean          # remove build artifacts
#
# Per-package targets (explicit, no magic):
#   make sdk-test       make sdk-build      make sdk-lint
#   make admin-dev      make admin-build
#   make docs-dev       make docs-build
#   make vscode-build

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

UV   := $(shell command -v uv 2>/dev/null || echo uv)
PNPM := $(shell command -v pnpm 2>/dev/null || echo pnpm)

.PHONY: help bootstrap install install-all clean \
        test build lint format typecheck \
        sdk-test sdk-build sdk-lint sdk-format sdk-typecheck \
        admin-dev admin-build admin-lint \
        docs-dev docs-build docs-lint \
        vscode-build vscode-lint \
        backend-build \
        dev-all admin-up compose-up compose-down \
        doctor status

help:                            ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ── Setup ──────────────────────────────────────────────────────────────────

bootstrap:                       ## First-time setup (install uv, pnpm, hooks, deps)
	@./scripts/bootstrap.sh

install:                         ## Sync all deps (uv workspace + pnpm workspace)
	$(UV) sync --all-packages
	$(PNPM) -r install

install-all:                     ## Sync all deps including dev/test groups
	$(UV) sync --all-packages --group test
	$(PNPM) -r install

clean:                           ## Remove build artifacts
	rm -rf packages/sdk/dist packages/sdk/build .pytest_cache .mypy_cache .ruff_cache
	rm -rf apps/admin/.next apps/docs/.next apps/vscode-extension/out apps/backend/dist
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true

# ── Aggregate targets ──────────────────────────────────────────────────────

test: sdk-test                   ## Run all tests (currently sdk only)

build: sdk-build admin-build docs-build vscode-build ## Build every package for release

lint: sdk-lint admin-lint docs-lint vscode-lint      ## Lint every package

format: sdk-format               ## Format all code (Python only for now)

typecheck: sdk-typecheck         ## Type-check all packages (Python only for now)

# ── SDK (Python) ───────────────────────────────────────────────────────────

sdk-test:                        ## Run sdk unit tests
	$(UV) run --package sagewai pytest packages/sdk/tests/ -m "not integration" -o "addopts="

sdk-build:                       ## Build sdk wheel + sdist
	rm -rf packages/sdk/dist
	$(UV) build --package sagewai

sdk-lint:                        ## Lint sdk with ruff
	$(UV) run --with ruff ruff check packages/sdk/sagewai/

sdk-format:                      ## Format sdk with ruff
	$(UV) run --with ruff ruff format packages/sdk/sagewai/ packages/sdk/tests/

sdk-typecheck:                   ## Type-check sdk with mypy
	$(UV) run --with mypy mypy packages/sdk/sagewai/

# ── Admin UI (Next.js) ─────────────────────────────────────────────────────

admin-dev:                       ## Start admin dev server on :3008
	$(PNPM) --filter @sagewai/admin dev

admin-build:                     ## Build admin production bundle
	$(PNPM) --filter @sagewai/admin build

admin-lint:                      ## Lint admin
	$(PNPM) --filter @sagewai/admin lint

# ── Docs (Next.js + Cloudflare Pages) ──────────────────────────────────────

docs-dev:                        ## Start docs dev server on :3010
	$(PNPM) --filter @sagewai/docs dev

docs-build:                      ## Build docs production bundle
	$(PNPM) --filter @sagewai/docs build

docs-lint:                       ## Lint docs
	$(PNPM) --filter @sagewai/docs lint

# ── VS Code extension ──────────────────────────────────────────────────────

vscode-build:                    ## Build vscode extension
	$(PNPM) --filter sagewai build 2>/dev/null || true  # no build script yet, safe no-op

vscode-lint:                     ## Lint vscode extension
	$(PNPM) --filter sagewai lint 2>/dev/null || true

# ── Backend Docker image ───────────────────────────────────────────────────

backend-build:                   ## Build backend Docker image locally
	$(UV) build --package sagewai --out-dir apps/backend/dist
	docker build -t sagewai-backend:dev apps/backend

# ── Dev orchestration ──────────────────────────────────────────────────────

dev-all:                         ## Run backend (FastAPI) + admin UI concurrently
	@echo "Starting backend on :8000 and admin on :3008..."
	@( $(UV) run --package sagewai sagewai admin serve --host 0.0.0.0 --port 8000 & \
	   $(PNPM) --filter @sagewai/admin dev & \
	   wait )

admin-up:                        ## Legacy docker stack launcher (delegates to sdk script)
	@./scripts/admin-up.sh

compose-up:                      ## Start full stack via root docker-compose.yml
	docker compose up -d

compose-down:                    ## Stop and remove full stack
	docker compose down

# ── Diagnostics ────────────────────────────────────────────────────────────

doctor:                          ## Check installation health
	$(UV) run --package sagewai sagewai doctor

status:                          ## Check infrastructure connectivity
	$(UV) run --package sagewai sagewai status
