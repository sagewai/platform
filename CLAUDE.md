# CLAUDE.md — session context for sagewai/platform

This file gives Claude (and any other agent starting a fresh session in
this checkout) the project context it needs to be useful on the first
message, without having to re-read the whole repo.

## What this is

`sagewai/platform` is a **private** monorepo containing the full Sagewai
stack: SDK, admin UI, backend Docker shell, docs site, and VS Code
extension. It is the primary development surface; the old `sagewai`,
`admin`, `docs`, and `vscode` repos were merged here in April 2026 and
archived.

Companion projects live in separate repos:
- `sagewai/web` — marketing site at sagewai.ai (Next.js + Cloudflare Workers)
- `sagewai/sagewai-*` — 17 thin client wrappers (TS, Go, Rust, Java, etc.)
- `sagewai/sagewai-enterprise` (future) — private cloud/SaaS management layer

## Layout

```
packages/sdk/                    Python SDK (sagewai on PyPI)
  sagewai/                       → cli, mcp, admin, gateway, fleet subpackages
  tests/                         → pytest suite (2904 tests)
  tests/test_smoke.py            → 29 smoke tests (no services)
  tests/test_perf.py             → 4 perf micro-benchmarks (no services)
  sagewai/examples/              → 23 runnable examples (01_hello_agent.py → 24_harness_agent.py)
apps/admin/                      Next.js admin UI
apps/docs/                       Next.js docs site
apps/backend/                    Dockerfile wrapping packages/sdk
apps/vscode-extension/           VS Code extension
brand/                           Logo, icon, favicon source of truth
scripts/                         bootstrap.sh, deploy-docs.sh, deploy-web.sh, release.sh
.changeset/                      Unified versioning config
.github/workflows/               ci-{sdk,admin,docs,vscode}.yml + release-* + tag-release
```

## Every Make target

Run `make help` for the live list; the ones you'll reach for:

| Target | What it runs | Duration |
|---|---|---|
| `make bootstrap` | First-time setup (uv + pnpm + workspace sync) | ~90s |
| `make smoke` | 29 fast smoke tests | ~0.1s |
| `make test` | Full SDK unit suite (2904 tests) | ~10s |
| `make perf` | 4 perf micro-benchmarks with fixed budgets | ~0.1s |
| `make build` | sdk wheel + admin + docs + vscode builds | ~2 min |
| `make dev-all` | Backend + admin UI concurrently | long-running |
| `make compose-up` | Full stack (postgres + redis + backend + admin) | long-running |

Package-scoped variants: `sdk-test`, `sdk-smoke`, `sdk-perf`, `sdk-build`, `admin-dev`, `docs-dev`, `backend-build`.

## Versioning and release

Unified semver via Changesets (`.changeset/config.json`). One `vX.Y.Z`
tag bumps sdk, admin, docs, backend, and vscode-extension to the same
version. Release flow:

```bash
pnpm changeset              # author a changeset
./scripts/release.sh        # compute next version + commit + tag
git push origin main --follow-tags
```

The tag push triggers every `release-*.yml` workflow in parallel.

## Licensing

- AGPL-3.0-or-later
- Copyright holder: **Ali Arda Diri**, Berlin, Germany (never "Sagewai, Inc.")
- Dual-licensed — commercial inquiries via `licensing@sagewai.ai`
- Contributors grant a broad relicense right via `CLA.md` (Harmony template)

If you are asked to add copyright headers or licensing language to new
files, use the exact phrasing from an existing file — do not invent new
variants.

## Deployment model

- **PyPI** (`sagewai`) — `release-sdk.yml` on tag push
- **GHCR** (`ghcr.io/sagewai/admin`, `ghcr.io/sagewai/backend`) — `release-admin.yml` / `release-backend.yml`
- **docs.sagewai.ai** — Cloudflare Workers Builds auto-deploy from this repo, root directory `apps/docs` (see `apps/docs/CLOUDFLARE.md` for the one-time dashboard connection)
- **sagewai.ai** (marketing) — separate repo `sagewai/web`, Cloudflare Workers Builds
- **VS Code Marketplace** — `release-vscode.yml`

**There is no GitHub Actions deploy workflow for the static sites** — Cloudflare polls the repo from its own side and builds on push. CI in this repo only runs build-verification and `wrangler --dry-run` as a safety net.

## Known issues you may encounter

1. **`sagewai[fastapi]` extra is missing `uvicorn`.** Workaround: `uv pip install uvicorn` after a fresh sync. A proper fix is to add `uvicorn` to the `[project.optional-dependencies]` `fastapi` array in `packages/sdk/pyproject.toml`.

2. **No `/health` route on the admin FastAPI.** The Dockerfile `HEALTHCHECK` and `docker-compose.yml` healthcheck both hit `/openapi.json` as a proxy. If a dedicated `/health` route is added to the SDK, update both.

3. **Git commit author email is `ardadiri@mac-mini.local` in all existing history.** This is Ali's local git identity default from the macOS hostname. Minor hostname leak. Do **not** rewrite history to fix. For any new commits you author in a session, pass `-c user.email=ardadiri@gmail.com` explicitly.

4. **Dependabot fires weekly PRs** across the monorepo and all 17 wrapper repos. They are routine action-version bumps; merge on sight unless a test fails.

## Governance

- Branch protection on `main`: PR required, code-owner review, linear history, `enforce_admins: true`, no force push, no delete.
- CODEOWNERS: `@sagecurator` (sole maintainer).
- Issues are **disabled** per the closed-governance model — community input goes through GitHub Discussions.
- `@sagecurator/maintainers` team is the only group with write access.

## Recent migration history

The monorepo was assembled from 4 previously-separate repos via
`git filter-repo --to-subdirectory-filter` on 2026-04-11. All commit
history is preserved. See the root `README.md` "Monorepo map" section
for the final layout.

## Test session prompt

If you are starting a dedicated testing session — running the SDK
examples, probing the CLI, testing the admin UI — use the prompt at
`TEST-SESSION-PROMPT.md` in this same directory as your starting
message. It gives Claude the exact workflow and constraints.

## Where to find more

- `README.md` — marquee README with 5-example tour, quickstart, videos
- `apps/docs/CLOUDFLARE.md` — docs-site deploy model
- `packages/sdk/pyproject.toml` — Python deps, scripts, pytest markers
- `packages/sdk/README.md` — SDK API surface
- `brand/README.md` — canonical brand asset rules
- `.changeset/README.md` — release flow
