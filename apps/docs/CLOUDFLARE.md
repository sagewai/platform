# Cloudflare deployment — docs.sagewai.ai

## How auto-deploy works

`docs.sagewai.ai` is served by a **Cloudflare Worker** named `docs` with
Workers Assets (see `wrangler.toml`), auto-rebuilt on every push to `main`
via **Cloudflare Workers Builds** (dashboard-side Git integration). No
GitHub Actions workflow runs the deploy — the trigger lives entirely on
Cloudflare's side.

This is the same mechanism the old `sagewai/docs` repo used. When that
repo was archived during the 2026-04-12 monorepo migration, the original
Cloudflare integration pointed at an archived repo and stopped working.
The fix is to reconnect the `docs` Worker to `sagewai/platform` with
`apps/docs` as the root directory.

## Connecting Cloudflare to `sagewai/platform` → `apps/docs`

One-time setup in the Cloudflare dashboard:

1. Go to <https://dash.cloudflare.com> → **Workers & Pages** → click the
   existing `docs` Worker (if it still exists) or create a new one via
   **Create** → **Workers** → **Import a repository**.
2. Authorize the Cloudflare GitHub App on the `sagewai` org if it isn't
   already, and grant access to `sagewai/platform`.
3. Under **Settings → Build configuration** (or during first setup):
   - **Repository:** `sagewai/platform`
   - **Branch:** `main`
   - **Root directory:** `apps/docs`
   - **Build command:** `pnpm install && pnpm --filter @sagewai/docs build`
   - **Deploy command:** `npx wrangler deploy`
   - **Build output directory:** `out`
4. Save. The first auto-deploy runs immediately; subsequent pushes to
   `main` under `apps/docs/**` auto-trigger new deploys.
5. Verify the custom domain `docs.sagewai.ai` is attached under
   **Settings → Domains & Routes**. If missing, add it — Cloudflare
   handles DNS and SSL provisioning automatically.

## Manual deploy (local)

For bootstrapping, emergency re-deploys, or pre-push validation:

```bash
./scripts/deploy-docs.sh              # full build + deploy
./scripts/deploy-docs.sh --dry-run    # build + wrangler --dry-run
./scripts/deploy-docs.sh --build-only # just verify the Next.js static export
```

Prereqs:
- `npx wrangler whoami` shows your account (run `npx wrangler login` if not)
- `./scripts/bootstrap.sh` has been run once to sync the workspace

## CI checks

`.github/workflows/ci-docs.yml` runs on every push and PR that touches
`apps/docs/**`:

1. Install deps
2. Build the Next.js static export
3. Verify `apps/docs/out` is populated
4. Run `wrangler deploy --dry-run` to validate the bundle without publishing

CI does **not** publish to Cloudflare. Auto-deploy is Cloudflare's job via
Workers Builds. CI is the safety net that catches build breakage before
Cloudflare tries (and potentially fails silently) to pull and build.

## Troubleshooting

### `docs.sagewai.ai` is serving stale content

1. Check Cloudflare dashboard → **Workers & Pages** → `docs` → **Deployments**.
   If the latest deployment is older than the latest push to `main`, the
   Git integration is broken (usually because it points at an archived
   repo or a stale branch).
2. Reconnect per the steps above, or run `./scripts/deploy-docs.sh` locally
   to force a fresh deploy.

### CI keeps passing but deploys fail on Cloudflare's side

Cloudflare Workers Builds logs live in the dashboard under the Worker's
**Deployments** tab. Expand a failed deployment to see build output.
Common causes: missing secrets (only if the build references them),
Node.js version mismatch (set `NODE_VERSION=20` under **Settings →
Variables and secrets → Build variables**), or `pnpm` not found (set
`PACKAGE_MANAGER=pnpm`).

### Custom domain not resolving

Verify under **Settings → Domains & Routes** that `docs.sagewai.ai` is
attached to this Worker, and check Cloudflare DNS for a proxied CNAME
pointing at the Worker's default subdomain.

## Related

- [`../../scripts/deploy-docs.sh`](../../scripts/deploy-docs.sh) — manual deploy helper
- [`../../.github/workflows/ci-docs.yml`](../../.github/workflows/ci-docs.yml) — build + dry-run CI
- `wrangler.toml` (this directory) — Worker name, assets directory config
