#!/usr/bin/env sh
# Sagewai backend container entrypoint.
#
# Execs the CMD passed by Docker (default: `sagewai admin serve ...`).
# Runs under tini (PID 1) so SIGTERM propagates to the Python process for
# graceful shutdown of in-flight FastAPI requests.
set -eu

# Sanity: DATABASE_URL is required for persistent mode. Emit a loud warning
# if it's missing so misconfigured deploys fail fast instead of starting in
# an in-memory mode that silently drops data on restart.
if [ -z "${DATABASE_URL:-}" ]; then
  echo "[sagewai-backend] WARNING: DATABASE_URL is not set — running in non-persistent mode" >&2
fi

exec "$@"
