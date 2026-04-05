UV := $(shell command -v uv 2>/dev/null || echo "$$HOME/.local/bin/uv")

.PHONY: install install-all test test-smoke lint format typecheck build clean \
       admin-serve doctor status \
       e2e-offline e2e-live e2e-cli e2e-all \
       publish-test publish-check help

# ── Setup ────────────────────────────────────────────────────────────────────

install:                          ## Install core dependencies
	$(UV) sync --all-packages

install-all:                      ## Install all dependencies including test/dev
	$(UV) sync --all-packages --group test

# ── Quality ──────────────────────────────────────────────────────────────────

test:                             ## Run unit tests (no external services)
	$(UV) run pytest tests/ -m "not integration" -o "addopts="

test-smoke:                       ## Run 29 smoke tests (fast, no deps)
	$(UV) run pytest tests/test_smoke.py -v

lint:                             ## Lint with ruff
	$(UV) run --with ruff ruff check sagewai/

format:                           ## Format with ruff
	$(UV) run --with ruff ruff format sagewai/ tests/

typecheck:                        ## Type-check with mypy
	$(UV) run --with mypy mypy sagewai/

# ── Build ────────────────────────────────────────────────────────────────────

build:                            ## Build wheel + sdist
	rm -rf dist/
	$(UV) build

clean:                            ## Remove build artifacts
	rm -rf dist/ build/ .pytest_cache .mypy_cache
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true

# ── Local services ───────────────────────────────────────────────────────────

admin-serve:                      ## Start admin API server (port 8000)
	$(UV) run sagewai admin serve --port 8000

doctor:                           ## Check installation health
	$(UV) run sagewai doctor

status:                           ## Check infrastructure connectivity
	$(UV) run sagewai status

# ── E2E testing ──────────────────────────────────────────────────────────────

e2e-offline:                      ## Run offline E2E tests (no API keys needed)
	@bash scripts/e2e-test.sh --offline

e2e-live:                         ## Run live E2E tests (needs API keys)
	@bash scripts/e2e-test.sh --live

e2e-cli:                          ## Test all CLI subcommands
	@bash scripts/e2e-test.sh --cli

e2e-all:                          ## Run full E2E suite
	@bash scripts/e2e-test.sh --all

# ── Publish pipeline ─────────────────────────────────────────────────────────

publish-check:                    ## Inspect package without uploading
	@bash scripts/check-package.sh

publish-test:                     ## Full TestPyPI round-trip (needs TESTPYPI_TOKEN)
	@bash scripts/test-publish.sh

# ── Help ─────────────────────────────────────────────────────────────────────

help:                             ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
