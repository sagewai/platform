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

# Podman-on-Mac compatibility: when Docker Desktop isn't running but a
# podman machine is, point DOCKER_HOST at podman's socket so docker
# compose / docker buildx work transparently. No-op when DOCKER_HOST is
# already set or when Docker Desktop is the active backend.
export DOCKER_HOST := env_var_or_default('DOCKER_HOST', shell('podman machine inspect --format "unix://{{.ConnectionInfo.PodmanSocket.Path}}" 2>/dev/null || echo ""'))

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

# ──────────────────────────────────────────────────────────────────────
# Autopilot end-to-end demo (admin backend + admin UI + autopilot enabled)
# ──────────────────────────────────────────────────────────────────────
# Prereq: sagewai-llm must be running on :8100. From sister repo, run:
#     cd ../sagewai-llm && just llm-up
#
# This recipe:
#   1. Confirms sagewai-llm is reachable.
#   2. Starts admin backend on :8000 with SAGEWAI_LLM_BASE_URL pointing at it.
#   3. Starts admin Next.js dev server on :3008.
#   4. After both come up, posts /api/v1/autopilot/enable so the goal route works.
#   5. Prints the demo URL.
autopilot-demo SAGEWAI_LLM_BASE_URL='http://localhost:8100':
    #!/usr/bin/env bash
    set -e
    trap 'kill 0 2>/dev/null' EXIT
    echo "→ checking sagewai-llm at {{SAGEWAI_LLM_BASE_URL}}..."
    if ! curl -sf {{SAGEWAI_LLM_BASE_URL}}/health >/dev/null; then
      echo "✗ sagewai-llm not reachable at {{SAGEWAI_LLM_BASE_URL}}"
      echo "  run: cd ../sagewai-llm && just llm-up"
      exit 1
    fi
    echo "✓ sagewai-llm healthy"
    echo "→ bootstrapping admin (idempotent: user, token, autopilot config, env)..."
    SAGEWAI_LLM_BASE_URL={{SAGEWAI_LLM_BASE_URL}} \
      uv run --package sagewai python scripts/dev-bootstrap-admin.py
    echo "→ starting admin backend on :8000..."
    SAGEWAI_LLM_BASE_URL={{SAGEWAI_LLM_BASE_URL}} \
    SAGEWAI_DEV_TRUST_LOCAL=1 \
    SAGEWAI_ALLOW_HOST_EXEC=1 \
      uv run --package sagewai sagewai admin serve --host 0.0.0.0 --port 8000 &
    echo "→ starting admin UI on :3008..."
    pnpm --filter @sagewai/admin dev &
    echo "→ waiting for admin backend to come up..."
    for i in $(seq 1 30); do
      if curl -sf http://localhost:8000/openapi.json >/dev/null 2>&1; then
        echo "✓ backend ready"
        break
      fi
      sleep 1
    done
    echo ""
    echo "─────────────────────────────────────────────────────"
    echo " Open: http://localhost:3008/autopilot"
    echo " Try goal: 'track competitor pricing daily'"
    echo " Expected: auto_route → competitive-research-daily ~0.92"
    echo "─────────────────────────────────────────────────────"
    echo " (Ctrl-C to stop both processes.)"
    wait

# Stop the autopilot-demo backend + UI by killing the dev ports.
autopilot-demo-down:
    -lsof -ti :8000 | xargs -r kill 2>/dev/null
    -lsof -ti :3008 | xargs -r kill 2>/dev/null
    @echo "✓ stopped admin processes on :8000 and :3008"

# ── Fleet workers ──────────────────────────────────────────────────────────
# Create/run fleet workers against the gateway. Auth via env:
#   SAGEWAI_ADMIN_URL (default http://localhost:8000), SAGEWAI_ADMIN_TOKEN.

# Register a worker so it appears in the admin Workers screen (no loop).
fleet-create name models *flags:
    uv run --package sagewai sagewai fleet run --register-only --name {{name}} --models {{models}} {{flags}}

# Register + run the worker daemon in the foreground (Ctrl-C to drain & stop).
fleet-run name models *flags:
    uv run --package sagewai sagewai fleet run --name {{name}} --models {{models}} {{flags}}

# Example fuller config: GPU pool, labels, concurrency.
fleet-run-gpu name:
    uv run --package sagewai sagewai fleet run --name {{name}} \
        --models gpt-4o,ollama/llama3:70b --pool gpu-cluster \
        --labels gpu=a100,zone=us-east --max-concurrent 4

# ── Fleet: single-user local (auto-auth, no manual token/login) ─────────────
# One-command-per-step path for your own machine. `fleet-demo-up` starts the
# gateway with loopback dev-trust (SAGEWAI_DEV_TRUST_LOCAL=1), so the fleet-*
# recipes below mint a short-lived admin token from localhost automatically.
# Single-user / single-org / LOOPBACK ONLY — never point these at a shared gateway.
#
# Typical flow (two terminals):
#   T1:  just fleet-demo-up
#   T2:  just fleet-selftest                         # prove it works (no LLM key)
#   T2:  just fleet-run-agent w1 gpt-4o-mini --env OPENAI_API_KEY=sk-...   # real agents
#   T3:  just fleet-approve-all                       # approve the worker
#   T3:  just fleet-enqueue helper "Summarize Sagewai." gpt-4o-mini

fleet_home := env_var_or_default('SAGEWAI_HOME', env_var('HOME') / '.sagewai')
fleet_url  := env_var_or_default('SAGEWAI_ADMIN_URL', 'http://127.0.0.1:8000')

# Start the local gateway for single-user fleet use (auto-creates an admin). Leave running.
fleet-demo-up:
    SAGEWAI_HOME="{{fleet_home}}" uv run --package sagewai python scripts/fleet-local-setup.py
    @echo "→ gateway on {{fleet_url}} — dev-trust on; fleet-* recipes auto-auth. Ctrl-C to stop."
    SAGEWAI_HOME="{{fleet_home}}" SAGEWAI_DEV_TRUST_LOCAL=1 \
        uv run --package sagewai sagewai admin serve --host 127.0.0.1 --port 8000

# Mint a short-lived admin token from the local dev-trust gateway (used by the recipes).
[private]
_fleet-token:
    @curl -fsS -X POST "{{fleet_url}}/api/v1/auth/refresh" \
        | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])"

# Run a worker that executes AGENTS (the agent_task_handler) — auto-auths. Pass the LLM
# key as a flag:  just fleet-run-agent w1 gpt-4o-mini --env OPENAI_API_KEY=sk-...
fleet-run-agent name models *flags:
    SAGEWAI_ADMIN_URL="{{fleet_url}}" SAGEWAI_ADMIN_TOKEN="$(just _fleet-token)" SAGEWAI_HOME="{{fleet_home}}" \
        uv run --package sagewai sagewai fleet run --name {{quote(name)}} --models {{quote(models)}} {{flags}} \
        --exec 'python -m sagewai.examples.fleet.agent_task_handler'

# Run a worker with a CUSTOM --exec command, quoted properly (fleet-run's *flags can't).
#   just fleet-run-exec w1 gpt-4o-mini 'python /path/handler.py' --env KEY=val
fleet-run-exec name models exec *flags:
    SAGEWAI_ADMIN_URL="{{fleet_url}}" SAGEWAI_ADMIN_TOKEN="$(just _fleet-token)" SAGEWAI_HOME="{{fleet_home}}" \
        uv run --package sagewai sagewai fleet run --name {{quote(name)}} --models {{quote(models)}} {{flags}} \
        --exec {{quote(exec)}}

# Assign a task to the fleet — auto-auths. The message is quoted, so spaces are fine.
#   just fleet-enqueue helper "Summarize Sagewai in one line." gpt-4o-mini
fleet-enqueue agent message model="gpt-4o-mini":
    SAGEWAI_ADMIN_URL="{{fleet_url}}" SAGEWAI_ADMIN_TOKEN="$(just _fleet-token)" \
        uv run --package sagewai sagewai fleet enqueue --agent {{quote(agent)}} --message {{quote(message)}} --model {{quote(model)}}

# Approve all pending workers (so you don't have to copy worker ids).
fleet-approve-all:
    #!/usr/bin/env bash
    set -euo pipefail
    token="$(just _fleet-token)"
    ids="$(curl -fsS "{{fleet_url}}/api/v1/fleet/workers" -H "Authorization: Bearer $token" \
      | python3 -c "import sys,json;d=json.load(sys.stdin);ws=d if isinstance(d,list) else d.get('workers') or d.get('items') or [];print(chr(10).join((w.get('worker_id') or w.get('id') or '') for w in ws if str(w.get('approval_status') or w.get('status') or '').lower().startswith('pend')))")"
    if [ -z "$ids" ]; then echo "no pending workers"; exit 0; fi
    while read -r wid; do
      [ -n "$wid" ] || continue
      curl -fsS -X POST "{{fleet_url}}/api/v1/fleet/workers/$wid/approve" -H "Authorization: Bearer $token" >/dev/null
      echo "approved $wid"
    done <<< "$ids"

# Prove the local fleet works end-to-end with NO LLM/key: register+approve a worker,
# enqueue a trivial task, run it once, print the captured output. Needs fleet-demo-up.
fleet-selftest:
    #!/usr/bin/env bash
    set -euo pipefail
    export SAGEWAI_ADMIN_URL="{{fleet_url}}" SAGEWAI_HOME="{{fleet_home}}"
    token="$(just _fleet-token)"; export SAGEWAI_ADMIN_TOKEN="$token"
    handler="$(mktemp)"
    printf 'import sys,json,os\nt=json.load(sys.stdin)\nm=(t.get("payload") or {}).get("message","")\nprint(f"[worker pid={os.getpid()}] ran {t[\"run_id\"]} -> {m.upper()}")\n' > "$handler"
    wid="$(uv run --package sagewai sagewai fleet run --register-only --name selftest-$$ --models gpt-4o-mini | grep -oiE '[0-9a-f-]{36}' | head -1)"
    curl -fsS -X POST "$SAGEWAI_ADMIN_URL/api/v1/fleet/workers/$wid/approve" -H "Authorization: Bearer $token" >/dev/null
    echo "✓ worker $wid registered + approved"
    uv run --package sagewai sagewai fleet enqueue --agent selftest --message "fleet works" --model gpt-4o-mini
    uv run --package sagewai sagewai fleet run --once --worker-id "$wid" --models gpt-4o-mini --exec "python $handler"

# Start backend + admin via Docker (scripts/admin-up.sh)
admin-up:
    ./scripts/admin-up.sh

# Start docs Next.js dev server on :3010
docs-dev:
    pnpm --filter @sagewai/docs dev

# Builds the backend/admin images from source on first run (compose `build:`) and
# reuses them after; a one-time "unauthorized" ghcr pull-attempt warning is harmless.
# Start the full stack (postgres + redis + backend + admin); `stack-up` forces a rebuild
compose-up:
    docker compose up -d

# Stop and remove the Docker Compose stack
compose-down:
    docker compose down

# Builds backend (from packages/sdk) + admin (Next.js) images entirely from your
# checkout — no prebuilt images, no host prereqs beyond Docker. Equivalent to
# `docker compose up -d --build`. Stop the stack with `just compose-down`.
# Build the backend + admin images from source, then start the full stack
stack-up:
    docker compose up -d --build

# ── Dependencies (data services only — no app containers) ─────────────────
# Per-service control over the data dependencies the SDK touches. Useful
# when you want, e.g. just NebulaGraph for Example 41, without spinning
# up the backend + admin containers. Each `*-up` target is idempotent
# (re-running brings up the service if it's stopped).

# Bring up ALL data dependencies (postgres + redis + nebula cluster)
deps-up:
    docker compose --profile nebula up -d postgres redis nebula-metad nebula-storaged nebula-graphd nebula-console

# Stop ALL data dependencies (does not touch backend/admin)
deps-down:
    docker compose --profile nebula stop postgres redis nebula-metad nebula-storaged nebula-graphd nebula-console

# Bring up postgres only
pg-up:
    docker compose up -d postgres

# Stop postgres
pg-down:
    docker compose stop postgres

# Bring up redis only
redis-up:
    docker compose up -d redis

# Stop redis
redis-down:
    docker compose stop redis

# Bring up the NebulaGraph cluster (3 services + 1-shot console init)
# Connect from Example 41 with: SAGEWAI_GRAPH_BACKEND=nebula python …
nebula-up:
    docker compose --profile nebula up -d nebula-metad nebula-storaged nebula-graphd nebula-console

# Stop the NebulaGraph cluster
nebula-down:
    docker compose --profile nebula stop nebula-metad nebula-storaged nebula-graphd nebula-console

# Bring up the Milvus standalone stack (etcd + minio + milvus)
# The SDK's MilvusVectorMemory connects at http://localhost:19530
milvus-up:
    docker compose --profile milvus up -d milvus-etcd milvus-minio milvus-standalone

# Stop the Milvus standalone stack
milvus-down:
    docker compose --profile milvus stop milvus-etcd milvus-minio milvus-standalone

# Run sagewai SDK installation health check
doctor:
    uv run --package sagewai sagewai doctor

# Run sagewai infrastructure connectivity check
status:
    uv run --package sagewai sagewai status

# ── Release ────────────────────────────────────────────────────────────────
# Cut a production release: dispatches the release-sdk workflow, which bumps
# the version from the latest tag, builds, tests, runs the acceptance gate,
# publishes to PyPI, and pushes the vX.Y.Z tag. BUMP = patch | minor | major.
# (Every push to main already publishes a .dev build to TestPyPI automatically.)
release BUMP="patch":
    @test "{{BUMP}}" = "patch" -o "{{BUMP}}" = "minor" -o "{{BUMP}}" = "major" \
        || { echo "BUMP must be patch|minor|major (got '{{BUMP}}')"; exit 1; }
    gh workflow run release-sdk.yml -f bump={{BUMP}}
    @echo "Dispatched prod release (bump={{BUMP}}). Follow it with: gh run watch"

# ── Test ───────────────────────────────────────────────────────────────────
# Full unit suite, e2e suite, lint / format / typecheck (CI hygiene).

# Run full SDK test suite (~3900 tests, ~17s) — alias for sdk-test
test: sdk-test

# Run SDK unit tests (full suite, ~17s) directly via pytest
sdk-test:
    uv run --package sagewai --group test pytest packages/sdk/tests/ -m "not integration and not perf" -o "addopts="

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
    uv run --package sagewai --group test pytest packages/sdk/tests/test_smoke.py -v -o "addopts="

# ── Perf ───────────────────────────────────────────────────────────────────
# Performance micro-benchmarks with fixed time budgets that fail CI on regression.

# Run all perf micro-benchmarks — alias for sdk-perf
perf: sdk-perf

# Run SDK performance micro-benchmarks
sdk-perf:
    uv run --package sagewai --group test pytest packages/sdk/tests/test_perf.py -v -m perf -o "addopts="

# Run the Plan 1.5 sandbox pool warm-acquire benchmark
bench-pool:
    cd packages/sdk && uv run pytest tests/test_perf.py::test_perf_pool_warm_acquire -v

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
