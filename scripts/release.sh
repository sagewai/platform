#!/usr/bin/env bash
#
# release.sh — orchestrated release via Changesets.
#
# Computes the next unified version from pending changesets, updates every
# package manifest (packages/sdk/pyproject.toml, apps/*/package.json),
# writes CHANGELOG.md, commits, tags, and pushes. The tag push triggers
# .github/workflows/release-*.yml which publish the actual artifacts in
# parallel (PyPI, GHCR admin, GHCR backend, Cloudflare Pages, VS Code
# Marketplace).
#
# Usage:
#   ./scripts/release.sh                 # interactive
#   ./scripts/release.sh --dry-run       # show planned bump without writing
#
# Prereqs:
#   - `.changeset/*.md` files authored via `pnpm changeset`
#   - `gh auth status` authenticated
#   - Clean git working tree on main

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
  esac
done

log() { printf '\033[36m[release]\033[0m %s\n' "$*"; }

# Require clean tree
if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree is dirty. Commit or stash first." >&2
  exit 1
fi

# Require we're on main
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "error: releases must originate from main, currently on $BRANCH" >&2
  exit 1
fi

# Require at least one pending changeset
CHANGESET_COUNT=$(find .changeset -maxdepth 1 -name "*.md" ! -name "README.md" 2>/dev/null | wc -l | tr -d ' ')
if [ "$CHANGESET_COUNT" -eq 0 ]; then
  echo "error: no pending changesets. Run 'pnpm changeset' to author one." >&2
  exit 1
fi

log "found $CHANGESET_COUNT pending changeset(s)"

if [ "$DRY_RUN" -eq 1 ]; then
  log "dry run — would run 'pnpm changeset version' and tag"
  pnpm changeset status
  exit 0
fi

# Let Changesets compute the next version and write CHANGELOG.md + bump
# package.json files for the JS packages declared in the fixed array.
log "running 'pnpm changeset version'..."
pnpm changeset version

# The Python sdk + backend aren't in pnpm, so we mirror the version manually.
# Read the new version from apps/admin/package.json (first item in fixed[]).
NEW_VERSION="$(node -e "console.log(require('./apps/admin/package.json').version)")"
log "new unified version: v$NEW_VERSION"

# Bump packages/sdk/pyproject.toml version in place
python3 - <<PY
import re, pathlib
p = pathlib.Path("packages/sdk/pyproject.toml")
t = p.read_text()
t = re.sub(r'(?m)^version\s*=\s*"[^"]+"', f'version = "$NEW_VERSION"', t, count=1)
p.write_text(t)
print(f"bumped packages/sdk/pyproject.toml -> $NEW_VERSION")
PY

git add -A
git commit -m "chore(release): v$NEW_VERSION"
git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"

log "tagged v$NEW_VERSION. Push with: git push origin main --tags"
