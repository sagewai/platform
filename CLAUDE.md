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

## Every task (justfile)

Run `just` for the live list; the ones you'll reach for:

| Recipe | What it runs | Duration |
|---|---|---|
| `just bootstrap` | First-time setup (uv + pnpm + just + workspace sync) | ~90s |
| `just smoke` | 29 fast smoke tests | ~0.1s |
| `just test` | Full SDK unit suite (2928 tests) | ~10s |
| `just perf` | 4 perf micro-benchmarks with fixed budgets | ~0.1s |
| `just build` | sdk wheel + admin + docs + vscode builds | ~2 min |
| `just dev-all` | Backend + admin UI concurrently | long-running |
| `just compose-up` | Full stack (postgres + redis + backend + admin) | long-running |
| `just prereqs` | Check all dev tools are installed | instant |

Package-scoped variants: `just sdk-test`, `just sdk-smoke`, `just sdk-perf`, `just sdk-build`, `just admin-dev`, `just docs-dev`, `just backend-build`, `just admin-e2e`.

See `DEVELOPMENT.md` for full onboarding instructions and prerequisites.

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

## CRITICAL — Admin panel setup and auth flow

**DO NOT bypass the first-time setup wizard.** This is a secure
environment. The admin panel MUST enforce this flow:

1. **First launch** → `GET /api/v1/setup/status` returns
   `{"setup_required": true}` → proxy redirects ALL routes to `/setup`.
2. **Setup wizard** (7 steps) → collects org name, admin email,
   admin password, app config → `POST /api/v1/setup` creates the
   administrator account and marks setup as done.
3. **After setup** → user is auto-logged in, redirected to dashboard.
4. **Returning visits** → `setup_required: false` → proxy checks
   for `sagewai_auth` cookie → no cookie → redirect to `/login`.
5. **Login** → `POST /api/v1/auth/login` with email/password →
   sets httpOnly cookie → dashboard loads.

**The core pipeline (MUST work end-to-end):**

    Setup → Org → Projects → LLMs → Agents → Runs → Observability

Without the first 4 links, nothing downstream works.

**Architecture:**
- `admin/state_file.py` — file-backed config store (`~/.sagewai/admin-state.json`)
- `admin/provider_probes.py` — async LLM provider detection (Ollama, LM Studio, cloud)
- `admin/serve.py` — complete FastAPI app factory with ALL routes (144 endpoints)
- `cli/admin.py` — thin wrapper: creates AdminStateFile + calls serve.py

**Project scoping (multi-tenancy):**
- Every entity has a `project_id` field: `null` = org-global, `"slug"` = project-scoped
- Strict isolation: agents in project A cannot see agents in project B
- Org-global resources (`project_id=null`) are visible to all projects
- Frontend: `ProjectProvider` context + `useProject()` hook
- API client injects `X-Project-ID` header on every request automatically
- Backend: `_project_id(request)` helper reads the header, passes to `_filter_by_project()`
- Project selector in sidebar: dropdown with "All Projects (Global)" + per-project list
- OTel logs tagged with `sagewai.project_id` for per-project Grafana filtering
- Applies to: agents, providers, runs, workflows, budgets, guardrails,
  notifications, connectors, tokens, eval datasets, fleet — everything

**Fleet (real SDK integration — `sagewai.fleet`):**
- `InMemoryFleetRegistry` — worker registration, approval, heartbeat, enrollment keys
- `FleetDispatcher` + `InMemoryTaskStore` — task claim/report with capability matching
- Workers register with: `models_supported`, `pool`, `labels` (including `project_id`)
- Dispatch matches by: model, pool, labels, project scope
- Cross-project isolation: healthcare worker can't claim finance tasks
- Enrollment keys for bulk onboarding with pool/model restrictions
- See Example 26 for full demo

**Training data pipeline (for Unsloth fine-tuning):**
- `GET /api/v1/training/export?format=alpaca` — JSONL export
- `GET /api/v1/training/stats` — sample counts by agent
- `POST /api/v1/training/samples/{id}/quality` — rate 1-5
- Formats: `alpaca` (instruction/input/output), `sharegpt` (conversations), `raw`
- Project-scoped: each project collects and exports its own training data
- Pipeline: Collect → Curate → Export → Unsloth fine-tune → Ollama deploy → $0/token
- See Example 25 for full demo

**Roles (4 personas — defined in `apps/admin/utils/roles.ts`):**
- **admin** — full access (manage system, build agents, train models, view analytics)
- **developer** — build agents, use playground, manage tools (no system config)
- **ml_engineer** — training, data, evaluations, intelligence (no system config)
- **viewer** — read-only dashboards, reports, cost analytics

Each role has specific `navGroups` (sidebar sections), `permissions`
(binary capabilities), and `favorites` (pinned sidebar items).

**State file schema (`~/.sagewai/admin-state.json`):**
- `setup_complete`, `setup_at` — setup wizard state
- `org_name`, `org_slug`, `contact_email`, `timezone` — org settings
- `admin` — admin credentials (email, PBKDF2 hash, role)
- `active_tokens` — list of valid auth tokens (last 10)
- `projects` — array of tenant projects (slug, name, environment, default_model)
- `providers` — array of LLM provider configs (name, type, api_key, status)
- `agents` — array of playground-created agent specs
- `prompt_logs` — saved prompt logs (from Share → Save as example)
- `saved_workflows` — workflow registry entries

**Observability stack (`docker-compose.observability.yml`):**
- **VictoriaMetrics** (:8428) — scrapes OTel collector's Prometheus endpoint (:8889) every 10s
- **VictoriaLogs** (:9428) — receives logs via OTLP HTTP from collector
- **OTel Collector** (:4317/:4318) — receives OTLP, exposes Prometheus on :8889
- **Grafana** (:3000) — dashboards (admin/admin, anonymous enabled)
- Start: `docker compose -f docker-compose.observability.yml up -d --build`
- Dashboard: "Sagewai Admin" — 5 rows, 14 panels (health, HTTP, status codes, OTel pipeline, logs)
- Key metrics: `http_server_duration_milliseconds` (histogram),
  `http_server_active_requests` (gauge), `http_server_response_size_bytes` (histogram)
- Labels: `http_target` (route), `http_status_code`, `service_name="sagewai-admin"`
- **IMPORTANT:** Do NOT use `prometheusremotewrite` exporter — it silently drops
  histograms and counters. Always use the `prometheus` exporter + VM scraping.
- Backend emits structured business events: `setup.completed`, `auth.login.*`,
  `agent.created`, `agent.run.*`, `provider.test.*`, `provider.configured`
- Health check noise filtered from logs pipeline

**Email notifications (API-based, no SMTP):**
- Supports Resend (`re_*` keys), SendGrid (`SG.*` keys), Postmark
- Auto-detects provider from API key prefix
- Configure: `EMAIL_API_KEY` + `EMAIL_FROM` env vars or per-channel in admin UI

**E2e tests (`apps/admin/e2e/`):**
- Playwright with browser-based auth via storageState
- Backend + frontend auto-started by playwright.config.ts
- Run: `just admin-e2e` or `pnpm --filter @sagewai/admin test:e2e`
- Auth setup project logs in via real browser, saves `.auth/user.json`

**Rules for any agent working on this codebase:**
- NEVER hardcode `setup_required: false` — always check real state.
- NEVER return placeholder auth tokens — always verify credentials.
- NEVER skip the setup wizard for "convenience" — it is the security
  boundary. Without it the platform is completely useless.
- NEVER add routes inline in `cli/admin.py` — use `admin/serve.py`.
- The admin state lives at `~/.sagewai/admin-state.json` (file-based
  for local dev; Postgres-backed in production).
- Password hashing uses PBKDF2-SHA256 with 600k iterations.
- The guided tour (driver.js) auto-starts after first setup — do not
  remove or disable it.
- All frontend components exist and are intact — if a page shows
  empty or broken, check the backend route first (`curl` the endpoint).

## Issue tracking policy

**When you encounter a problem too deep to fix in the current session,
file a GitHub issue instead of leaving it broken or half-done.**

Rules:
- Create the issue with `gh issue create` on `sagewai/platform`.
- **Always label** with one of the area labels:
  `sdk`, `admin`, `cli`, `mcp`, `api`, `gateway`, `fleet`, `memory`,
  `safety`, `docs`, `backend`, `vscode-extension`, `observability`,
  `e2e-tests`, `ci`.
- **Always label** with one priority: `bug`, `enhancement`, or `chore`.
- Title format: `[area] short description`
  e.g. `[admin] e2e: authenticated pages redirect to login due to silentRefresh`
- Body must include: **Problem**, **Expected**, **Repro steps**,
  and optionally **Proposed fix**.
- Reference the issue number in any follow-up PR.
- Never leave a known broken feature undocumented — if you can't
  fix it now, the issue IS the deliverable.

## Known issues you may encounter

1. ~~`sagewai[fastapi]` extra missing `uvicorn`~~ **FIXED** in PR #48.

2. ~~No `/health` route on the admin FastAPI~~ **FIXED** in PR #41.
   Routes: `GET /api/v1/health/summary` and `/api/v1/health/detailed`.

3. **Git commit author email is `ardadiri@mac-mini.local` in all existing history.** Do **not** rewrite history. Pass `-c user.email=ardadiri@gmail.com` on every commit.

4. **Dependabot fires weekly PRs** across the monorepo and all 17 wrapper repos. Routine action-version bumps; merge on sight unless a test fails.

5. **macOS: always use `localhost` (not `127.0.0.1`) and bind to `0.0.0.0`.** macOS resolves `localhost` to `::1` (IPv6). The CLI default `--host 0.0.0.0` is correct.

6. ~~P2 API routes still missing~~ **FIXED** in PR #58 — all 144
   endpoints now served. Issue #44 closed.

7. ~~OTel custom metrics not appearing~~ **FIXED** in PR #66 — switched
   from `prometheusremotewrite` (silently dropped histograms/counters)
   to Prometheus scrape endpoint + VictoriaMetrics scraping. 25 metrics
   now visible in Grafana. Never use `prometheusremotewrite` again.

8. **`@sagecurator/ui` is fully decommissioned.** Zero references
   remain in the codebase (PR #59). The compat layer is at
   `apps/admin/components/ui/legacy.tsx`. Never re-add references
   to `@sagecurator/ui` or `@sagecurator` packages.

9. **Workflow builder `--color-info` token** was missing (PR #61).
   If you add new brand tokens, always define them in BOTH the
   light-mode `@theme` block AND the `[data-theme="dark"]` block
   in `apps/admin/app/brand-tokens.css`.

## Governance

- Branch protection on `main`: PR required, code-owner review, linear history, `enforce_admins: true`, no force push, no delete.
- CODEOWNERS: `@sagecurator` (sole maintainer).
- Issues are **enabled** — use area labels (`sdk`, `admin`, `cli`, etc.) and priority labels (`bug`, `enhancement`, `chore`).
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
