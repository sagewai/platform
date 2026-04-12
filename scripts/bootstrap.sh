#!/usr/bin/env bash
#
# bootstrap.sh — first-time setup for the Sagewai monorepo.
#
# Installs uv and pnpm if they're missing, then syncs both workspaces and
# installs git hooks. Idempotent: safe to re-run.
#
# Usage:
#   ./scripts/bootstrap.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m[bootstrap]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[bootstrap]\033[0m %s\n' "$*" >&2; }

# ── uv ──────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  log "installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
log "uv: $(uv --version)"

# ── just ───────────────────────────────────────────────────────────────
if ! command -v just >/dev/null 2>&1; then
  log "installing just..."
  if command -v brew >/dev/null 2>&1; then
    brew install just
  elif command -v cargo >/dev/null 2>&1; then
    cargo install just
  else
    curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to "$HOME/.local/bin"
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi
log "just: $(just --version)"

# ── Node / pnpm ─────────────────────────────────────────────────────────
if ! command -v node >/dev/null 2>&1; then
  err "node is not installed. Install Node.js 20+ (https://nodejs.org) and re-run."
  exit 1
fi
log "node: $(node --version)"

if ! command -v pnpm >/dev/null 2>&1; then
  log "installing pnpm via corepack..."
  corepack enable
  corepack prepare pnpm@latest --activate
fi
log "pnpm: $(pnpm --version)"

# ── Python workspace sync ───────────────────────────────────────────────
log "syncing Python workspace (uv sync --all-packages --group test)..."
uv sync --all-packages --group test

# ── JS workspace install ────────────────────────────────────────────────
log "syncing JS workspace (pnpm -r install)..."
pnpm -r install

# ── Git hooks (optional, non-fatal) ─────────────────────────────────────
if [ -d "$REPO_ROOT/.git" ] && [ -d "$REPO_ROOT/.githooks" ]; then
  log "installing git hooks from .githooks/..."
  git config core.hooksPath .githooks
fi

log "bootstrap complete. Try 'just smoke' or 'just dev-all'."
