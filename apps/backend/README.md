# sagewai-backend

Single-process, single-org self-hosted backend for the Sagewai platform
(`sagewai admin serve`). Published to `ghcr.io/sagewai/backend:<version>`
on every release tag.

This is a thin deploy shell around the `sagewai` Python package. The actual
backend code — FastAPI routes, controller, workflow store, analytics, gateway,
fleet dispatcher — all lives in `packages/sdk/sagewai/` as subpackages of the
`sagewai` PyPI package. This image just bundles that package with a minimal
Python runtime and starts it.

## Storage model — read before deploying

This backend is **single-process and single-org**. It is not a multi-tenant
service.

| Store | Backend | Notes |
|---|---|---|
| Admin state (connections, providers, agents, profiles) | JSON file (`~/.sagewai/admin-state.json`) | **Mount a persistent volume** so data survives container restarts. |
| Sealed revocation / replay log | Postgres (`DATABASE_URL` / `SAGEWAI_DATABASE_URL`) | Only wired when a database URL is set; omitting it disables these routes. |
| Fleet registry | **In-memory** | Resets on restart. Workers re-register on reconnect. |
| Task store | **In-memory** | Resets on restart. In-flight tasks are lost on restart. |
| Harness store (LLM proxy policies, keys, spend, audit) | **In-memory** | Resets on restart. `PostgresHarnessStore` is the future durable path. |

**`X-Project-ID` is an organizational filter, not tenant isolation.** It
scopes records to a project namespace but any caller who can reach the admin
API can supply any project ID. Do not use this to enforce security boundaries
between untrusted tenants.

**Provider secrets are encrypted at rest** with a master key. Set
`SAGEWAI_MASTER_KEY` to a Fernet key, or mount a key file at
`~/.sagewai/admin-master.key` on a persistent volume. **Losing the master key
makes all stored secrets unrecoverable.**

Durable Postgres-backed stores for fleet, tasks, and harness, plus proper
multi-tenant isolation, are on the roadmap.

## Quickstart

```bash
docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://sagewai:sagewai@host.docker.internal:5432/sagewai \
  -e SAGEWAI_MASTER_KEY=<your-fernet-key> \
  -v sagewai-state:/home/sagewai/.sagewai \
  ghcr.io/sagewai/backend:latest
```

Then browse <http://localhost:8000/docs> for the OpenAPI explorer.

For the full stack (postgres + redis + backend + admin UI) use the root
`docker-compose.yml` at the monorepo root:

```bash
docker compose up -d
```

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | No | — | Postgres connection string. Also accepted as `SAGEWAI_DATABASE_URL` (takes precedence). Enables Sealed revocation + replay routes. Defaults to SQLite at `~/.sagewai/db/sagewai.db` if unset. |
| `SAGEWAI_DATABASE_URL` | No | — | Same as `DATABASE_URL`; takes precedence when both are set. |
| `REDIS_URL` | No | — | Redis connection string for pub/sub and caching. |
| `SAGEWAI_MASTER_KEY` | Recommended | — | Fernet key for encrypting provider secrets at rest. Container startup fails closed (raises `AdminKeyMissing`) if absent and `SAGEWAI_RUNTIME=container`. |
| `SAGEWAI_RUNTIME` | Set by image | `container` | Informational tag used by the image; does not control host-exec policy (see `SAGEWAI_ALLOW_HOST_EXEC`). |
| `SAGEWAI_ALLOW_HOST_EXEC` | No | unset (denied) | Host-backed execution (on-host NullBackend / bash / stdio MCP) is **disabled by default** everywhere. Set to `1` to enable it — required for local self-hosted autopilot or any workflow that runs code directly on the host. |
| `SAGEWAI_LOG_LEVEL` | No | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `SAGEWAI_PROVIDER_*` | Varies | — | Provider API keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. See the SDK docs for the full list. |
| `SAGEWAI_ADMIN_TOKEN` | No | — | Static bearer token for admin UI auth. Omit for open dev mode. |

## Ports

- **8000** — FastAPI admin + gateway HTTP API.

## Health check

`GET /api/v1/health/summary` returns 200 whenever FastAPI is up. The
Dockerfile wires this as a `HEALTHCHECK` so orchestrators (compose, k8s)
can track readiness.

## Volume mounts

The image runs as non-root user `sagewai` (uid 1001). Mount a persistent volume
to `/home/sagewai/.sagewai` (or `/var/lib/sagewai`, owned by the `sagewai` user)
to preserve all state across container restarts:

- `config/admin-state.json` — connections, providers, agents, and Sealed profiles.
- `db/sagewai.db` — the SQLite database used when `DATABASE_URL` is unset.
- the master encryption key for provider secrets, if you use file-based key
  custody instead of `SAGEWAI_MASTER_KEY`.

**Losing these is unrecoverable.** Back them up, or use Postgres + an external
secrets manager.

## Building locally

This image expects the SDK wheel to be pre-built into `apps/backend/dist/`.
From the monorepo root:

```bash
uv build --package sagewai --out-dir apps/backend/dist
docker build -t sagewai-backend:dev apps/backend
docker run --rm -p 8000:8000 sagewai-backend:dev
```

If `dist/` is empty the Dockerfile falls back to `pip install sagewai[fastapi,postgres]`
from PyPI, so the image can also be built standalone outside the monorepo.

## Release pipeline

On a `v*.*.*` git tag, `.github/workflows/release-backend.yml`:

1. Builds the SDK wheel from `packages/sdk/`.
2. Copies it into `apps/backend/dist/`.
3. Runs `docker buildx build --platform linux/amd64,linux/arm64 --push`.
4. Publishes the multi-arch manifest to `ghcr.io/sagewai/backend:<tag>` and
   `ghcr.io/sagewai/backend:latest`.

## See also

- [`packages/sdk/`](../../packages/sdk/) — the `sagewai` Python package
- [`apps/admin/`](../admin/) — Next.js admin UI that talks to this backend
- [`docker-compose.yml`](../../docker-compose.yml) — full stack launch
