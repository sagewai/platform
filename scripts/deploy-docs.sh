#!/usr/bin/env bash
#
# deploy-docs.sh — manual one-off deploy for docs.sagewai.ai.
#
# Normally docs.sagewai.ai auto-deploys on push to main via Cloudflare
# Workers Builds (dashboard-side Git integration against sagewai/platform,
# root directory apps/docs). This script is for:
#
#   - bootstrapping the first deploy before the dashboard connection exists
#   - emergency re-deploys when auto-deploy is down
#   - verifying a build locally before pushing
#
# Prereqs:
#   - wrangler authenticated: npx wrangler whoami
#   - pnpm installed and the workspace synced (./scripts/bootstrap.sh)
#
# Usage:
#   ./scripts/deploy-docs.sh                 # full build + deploy
#   ./scripts/deploy-docs.sh --dry-run       # build + wrangler --dry-run (no publish)
#   ./scripts/deploy-docs.sh --build-only    # just verify the Next.js static export

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS_DIR="$REPO_ROOT/apps/docs"

cd "$REPO_ROOT"

log() { printf '\033[36m[deploy-docs]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[deploy-docs]\033[0m %s\n' "$*" >&2; }

MODE=deploy
for arg in "$@"; do
  case "$arg" in
    --dry-run)    MODE=dry-run ;;
    --build-only) MODE=build-only ;;
    -h|--help)
      sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      err "unknown flag: $arg"
      exit 2
      ;;
  esac
done

# ── Build ──────────────────────────────────────────────────────────────
log "building docs (Next.js static export → apps/docs/out/) ..."
pnpm --filter @sagewai/docs build

if [ ! -d "$DOCS_DIR/out" ] || [ -z "$(ls -A "$DOCS_DIR/out" 2>/dev/null)" ]; then
  err "apps/docs/out is missing or empty — build failed"
  exit 1
fi
log "out/ contains $(find "$DOCS_DIR/out" -type f | wc -l | tr -d ' ') files"

if [ "$MODE" = "build-only" ]; then
  log "build-only mode — stopping here"
  exit 0
fi

# ── Deploy ─────────────────────────────────────────────────────────────
cd "$DOCS_DIR"

if [ "$MODE" = "dry-run" ]; then
  log "running wrangler --dry-run (no publish)"
  npx wrangler deploy --dry-run
else
  log "running wrangler deploy (publishing to ghcr-equivalent of docs.sagewai.ai)"
  npx wrangler deploy
  log "done — visit https://docs.sagewai.ai"
fi
