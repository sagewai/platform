#!/usr/bin/env bash
#
# deploy-web.sh — manual one-off deploy for sagewai.ai (marketing site).
#
# Note: sagewai/web is a SEPARATE repo from sagewai/platform. This script
# exists here for convenience when doing a coordinated multi-site deploy
# (e.g., releasing a new brand identity across web + docs on the same day),
# but normally you'd run `pnpm deploy` from inside the sagewai/web clone.
#
# Like docs, sagewai.ai should auto-deploy on push to main via Cloudflare
# Workers Builds. This script is for bootstrapping, emergencies, and local
# verification.
#
# Prereqs:
#   - wrangler authenticated: npx wrangler whoami
#   - sagewai/web cloned at $WEB_DIR (defaults to ~/se/projects/sagewai/web)
#
# Env overrides:
#   WEB_DIR   Path to the sagewai/web checkout
#
# Usage:
#   ./scripts/deploy-web.sh              # full build + deploy
#   ./scripts/deploy-web.sh --dry-run    # build + wrangler --dry-run

set -euo pipefail

WEB_DIR="${WEB_DIR:-$HOME/se/projects/sagewai/web}"

log() { printf '\033[36m[deploy-web]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[deploy-web]\033[0m %s\n' "$*" >&2; }

if [ ! -d "$WEB_DIR/.git" ]; then
  err "WEB_DIR=$WEB_DIR is not a git repo. Clone sagewai/web first:"
  err "  git clone git@github.com:sagewai/web.git $WEB_DIR"
  exit 1
fi

MODE=deploy
for arg in "$@"; do
  case "$arg" in
    --dry-run)    MODE=dry-run ;;
    --build-only) MODE=build-only ;;
    -h|--help)
      sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      err "unknown flag: $arg"
      exit 2
      ;;
  esac
done

cd "$WEB_DIR"

log "syncing deps ..."
pnpm install --frozen-lockfile=false

log "building ..."
pnpm build

if [ ! -d out ] || [ -z "$(ls -A out 2>/dev/null)" ]; then
  err "out/ is missing or empty — build failed"
  exit 1
fi
log "out/ contains $(find out -type f | wc -l | tr -d ' ') files"

if [ "$MODE" = "build-only" ]; then
  log "build-only mode — stopping here"
  exit 0
fi

if [ "$MODE" = "dry-run" ]; then
  log "running wrangler --dry-run (no publish)"
  npx wrangler deploy --dry-run
else
  log "running wrangler deploy"
  npx wrangler deploy
  log "done — visit https://sagewai.ai"
fi
