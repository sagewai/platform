#!/usr/bin/env bash
#
# admin-up.sh — Single-command launch for the full Sagewai admin stack.
#
# Starts the Python admin API backend on :8000 and then runs the
# pre-built admin UI Docker image on :3008 pointing at it.
#
# Requirements:
#   - uv               (for the backend)
#   - docker / podman  (for the UI image)
#   - Local Sagewai SDK install (run `make install` first)
#
# Env overrides:
#   ADMIN_IMAGE   Docker image to run (default: ghcr.io/sagewai/admin:latest)
#   API_PORT      Backend port (default: 8000)
#   UI_PORT       UI port (default: 3008)
#
# Usage:
#   make admin-up
#   # or directly:
#   ./scripts/admin-up.sh
#
# Press Ctrl+C to stop both processes cleanly.

set -euo pipefail

ADMIN_IMAGE="${ADMIN_IMAGE:-ghcr.io/sagewai/admin:latest}"
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-3008}"

UV="${UV:-uv}"
DOCKER="${DOCKER:-docker}"

# ── Preflight checks ────────────────────────────────────────────────────────
if ! command -v "$UV" >/dev/null 2>&1; then
  echo "error: uv not found on PATH. Install from https://docs.astral.sh/uv/" >&2
  exit 1
fi
if ! command -v "$DOCKER" >/dev/null 2>&1; then
  echo "error: docker (or podman) not found on PATH." >&2
  exit 1
fi

# ── Cleanup on exit ─────────────────────────────────────────────────────────
BACKEND_PID=""
UI_CONTAINER="sagewai-admin-ui-$$"

cleanup() {
  local code=$?
  echo ""
  echo "─── shutting down admin stack ──────────────────────────────"
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "stopping backend (pid $BACKEND_PID)..."
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  if "$DOCKER" ps --format '{{.Names}}' | grep -q "^${UI_CONTAINER}$"; then
    echo "stopping UI container ${UI_CONTAINER}..."
    "$DOCKER" stop "$UI_CONTAINER" >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

# ── Start backend ───────────────────────────────────────────────────────────
echo "─── starting Sagewai backend on :${API_PORT} ───────────────"
"$UV" run sagewai admin serve --port "$API_PORT" &
BACKEND_PID=$!

# Wait for backend /health (or root /) to answer.
echo "waiting for backend to be ready..."
for i in $(seq 1 60); do
  if curl -sf "http://localhost:${API_PORT}/health" >/dev/null 2>&1 \
  || curl -sf "http://localhost:${API_PORT}/"         >/dev/null 2>&1; then
    echo "backend ready."
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "error: backend exited before becoming ready." >&2
    exit 1
  fi
  sleep 0.5
done

# ── Pull the admin image if missing ────────────────────────────────────────
if ! "$DOCKER" image inspect "$ADMIN_IMAGE" >/dev/null 2>&1; then
  echo "─── pulling ${ADMIN_IMAGE} ─────────────────────────────────"
  "$DOCKER" pull "$ADMIN_IMAGE"
fi

# ── Run the UI container in the foreground ──────────────────────────────────
# `host.docker.internal` resolves to the host from inside the container on
# both Docker Desktop and podman-machine; fall back to host-gateway on Linux.
HOST_HOST="host.docker.internal"
HOST_GATEWAY_ARGS=()
if [[ "$(uname -s)" == "Linux" ]]; then
  HOST_GATEWAY_ARGS=(--add-host "host.docker.internal:host-gateway")
fi

echo "─── starting admin UI on :${UI_PORT} ───────────────────────"
echo "open http://localhost:${UI_PORT}"
echo ""

"$DOCKER" run --rm \
  --name "$UI_CONTAINER" \
  -p "${UI_PORT}:3008" \
  "${HOST_GATEWAY_ARGS[@]}" \
  -e "NEXT_PUBLIC_ADMIN_API_URL=http://${HOST_HOST}:${API_PORT}/admin" \
  "$ADMIN_IMAGE"
