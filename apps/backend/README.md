# sagewai-backend

Production Docker image for the Sagewai FastAPI backend (`sagewai admin serve`).
Published to `ghcr.io/sagewai/backend:<version>` on every release tag.

This is a thin deploy shell around the `sagewai` Python package. The actual
backend code — FastAPI routes, controller, workflow store, analytics, gateway,
fleet dispatcher — all lives in `packages/sdk/sagewai/` as subpackages of the
`sagewai` PyPI package. This image just bundles that package with a minimal
Python runtime and starts it.

## Quickstart

```bash
docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgres://sagewai:sagewai@host.docker.internal:5432/sagewai \
  -e REDIS_URL=redis://host.docker.internal:6379 \
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
| `DATABASE_URL` | Yes (for persistence) | — | Postgres connection string. In-memory fallback if unset, warning emitted. |
| `REDIS_URL` | No | — | Redis connection string for pub/sub and caching. |
| `SAGEWAI_LOG_LEVEL` | No | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `SAGEWAI_PROVIDER_*` | Varies | — | Provider API keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. See the SDK docs for the full list. |
| `SAGEWAI_ADMIN_TOKEN` | No | — | Static bearer token for admin UI auth. Omit for open dev mode. |

## Ports

- **8000** — FastAPI admin + gateway HTTP API.

## Health check

`GET /openapi.json` returns 200 whenever FastAPI is up, without requiring
any database connection. The Dockerfile wires this as a `HEALTHCHECK` so
orchestrators (compose, k8s) can track readiness without hitting the DB.
The SDK does not currently expose a dedicated `/health` route — that's a
known gap tracked separately; once added, update this field and the
`HEALTHCHECK` line in the Dockerfile.

## Volume mounts

The image runs as non-root user `sagewai` (uid 1001). If you need persistent
local state (e.g., sqlite fallback, artifact storage), mount a volume to
`/var/lib/sagewai` which is owned by the `sagewai` user.

## Building locally

This image expects the SDK wheel to be pre-built into `apps/backend/dist/`.
From the monorepo root:

```bash
uv build --package sagewai --out-dir apps/backend/dist
docker build -t sagewai-backend:dev apps/backend
docker run --rm -p 8000:8000 sagewai-backend:dev
```

If `dist/` is empty the Dockerfile falls back to `pip install sagewai` from
PyPI, so the image can also be built standalone outside the monorepo.

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
