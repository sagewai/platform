# CLAUDE.md — session context for sagewai/platform

> ⚠️ **INTERNAL — current state.** This file references the private companion repo `sagewai/atelier` and contains strategic context that does not belong on a public surface. It is replaced with a public-only version (technical conventions only, no atelier reference) at the just-before-go-live cutover (tracker: issue #202 §1).

This file gives Claude (and any other agent starting a fresh session in
this checkout) the project context it needs to be useful on the first
message, without having to re-read the whole repo.

## What this is

`sagewai/platform` is a **currently-private** monorepo containing the full Sagewai
stack: SDK, admin UI, backend Docker shell, docs site, and VS Code
extension. It is the primary development surface; the old `sagewai`,
`admin`, `docs`, and `vscode` repos were merged here in April 2026 and
archived. **The repo flips PUBLIC at v1.0 launch (target 2026-05-29).**

Companion repos:
- `sagewai/atelier` — **PRIVATE** companion holding strategic, design, and planning content: vision, six-month roadmap, design specs (Sealed phases, autopilot, sandbox, plan ART), implementation plans, positioning canonical-lines meta-doc, monetization vignettes, moat audit. **Always check `sagewai/atelier` first when looking for strategic context.** See `## Repository split` below for path conventions.
- `sagewai/web` — marketing site at sagewai.ai (Next.js + Cloudflare Workers)
- `sagewai/sagewai-*` — 17 thin client wrappers (TS, Go, Rust, Java, etc.)
- `sagewai/sagewai-llm` — **PRIVATE** proprietary hosted blueprint service (the autopilot brain). See below.
- `sagewai/sagewai-enterprise` (future) — private cloud/SaaS management layer

## Repository split (INTERNAL — until go-live cutover)

This is a public-bound monorepo. **Strategic, design, and planning content lives in `sagewai/atelier`, NOT here.** Decision locked 2026-05-01. Tracker: issue #202.

**Stays in `sagewai/platform` (this repo, going public):**
- All code (`packages/sdk`, `apps/admin`, `apps/docs`, `apps/backend`, `apps/vscode-extension`)
- `docs/architecture/*.md` — technical canonical contract (mirrored to public docs site)
- `docs/positioning/one-pager.md` — public-facing one-pager
- `docs/operations/`, `docs/runbooks/` — operational docs (audited under #202 §1)
- Top-level: README, LICENSE, COMMERCIAL-LICENSE, LICENSE_FAQ, TRADEMARK, CONTRIBUTING, CODE_OF_CONDUCT, CLA, SECURITY, SUPPLY-CHAIN, DEVELOPMENT, CHANGELOG

**Lives in `sagewai/atelier` (private companion):**
- `docs/superpowers/specs/` — design specs (Sealed phases I-V, autopilot, sandbox, plan ART, launch coord, positioning evolution)
- `docs/superpowers/plans/` — implementation plans (autopilot, sealed, AgentCore, surface propagation, item 1a, future items)
- `docs/vision/` — six-month strategic vision, monetization vignettes, moat audit
- `docs/v1.1-roadmap.md` — internal forward-looking roadmap
- `docs/positioning/canonical-lines.md` — internal canonical-line meta-doc (the public one-pager itself stays in platform)
- Future: launch blog post draft, memory soak report, launch-day runbook

**Path conventions are preserved.** Files have the same paths in atelier as they had in platform pre-migration. To reference a strategic doc, prepend the repo name: e.g. `sagewai/atelier:docs/superpowers/specs/2026-05-01-sagewai-1.0-launch-coordination-design.md`.

**Working rules:**
- Before adding any strategic / branding / positioning / monetization / roadmap doc, decide which repo it belongs in. If it's not technical guidance for code in this repo, it goes to `sagewai/atelier`.
- `.gitignore` rules in `sagewai/platform` block re-introduction of `docs/superpowers/`, `docs/vision/`, `docs/v1.1-roadmap.md`, `docs/positioning/canonical-lines.md`. If you hit a gitignore wall trying to land one of those paths, that's the safety net — the file belongs in atelier, not here.
- Implementation plans (technical guidance) live in `sagewai/atelier:docs/superpowers/plans/` because they reference strategic context (launch coord spec, lane structure, internal sequencing). The implementation PRs themselves still target `sagewai/platform`.
- When referencing strategic docs in commit messages or PR descriptions, prefer indirect references ("per the implementation plan in atelier") over direct path mentions. Casual readers of public commit history (post-go-live) shouldn't be tipped off about specific strategic content.
- The history scrub via `git filter-repo --invert-paths` on `sagewai/platform` is deferred to just-before-go-live (per #202 §1). Until then, proprietary content remains in past-commit history of platform but is gone from `main` going forward, and the repo remains PRIVATE so the leak window is internal-only.

## Layout

```
packages/sdk/                    Python SDK (sagewai on PyPI)
  sagewai/                       → cli, mcp, admin, gateway, fleet, sandbox, sealed, harness subpackages
  sagewai/autopilot/             → Autopilot framework (Plans 1-8, see below)
  sagewai/harness/               → LLM Harness — smart proxy for AI coding tools (see below)
  tests/                         → pytest suite (~3600 tests)
  tests/autopilot/               → 663 autopilot framework tests
  tests/test_smoke.py            → 33 smoke tests (no services)
  tests/test_perf.py             → 4 perf micro-benchmarks (no services)
  tests/test_admin_autopilot.py  → 40 admin autopilot route tests
  sagewai/examples/              → 28 runnable examples (01_hello_agent.py → 28_autopilot_quickstart.py)
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

## Autopilot system (Plans 1-8 + I/J/K, PRs #97-#106 + #268-#271)

The autopilot lets operators state a goal in plain English and have the
platform design, provision, run, and improve the agents that deliver it.

**Architecture (OSS side — `sagewai.autopilot.*`):**
```
sagewai/autopilot/               Blueprint, SlotSpec, AgentGraph, Mission (Plan 1)
sagewai/autopilot/sagewai_llm/   SagewaiLLMClient, HMAC signing, cache (Plan 2)
sagewai/autopilot/routing/       GoalRouter, ConfidenceConfig, SlotExtractor (Plan 3)
sagewai/autopilot/controller/    AutopilotController, MissionDriver (Plan 4)
sagewai/autopilot/curator/       Curator, Promoter, TrainingDataset (Plan 5)
sagewai/autopilot/eval_harness/  EvalHarness, 52 golden goals (Plan 6)
sagewai/autopilot/healing/       HealthMonitor, HealingEngine (Plan 8)
sagewai/admin/autopilot_*.py     6 admin API routes (Plan 7)
apps/admin/app/autopilot/        Frontend: goal input, plan preview, missions (Phase 2)

-- Tier 3 integrations (Plans I/J/K — PRs #268/#270/#271) --
sagewai/autopilot/controller/fleet_match.py    match_workers() — capability matcher (Plan I)
sagewai/autopilot/controller/fleet_adapter.py  FleetMissionAdapter — fleet dispatch integration (Plan I)
sagewai/autopilot/tool_risk_profile.py         SandboxTier IntEnum, get_tier(), is_downgrade() (Plan J)
sagewai/autopilot/controller/tool_runner.py    ToolRunner — sandbox-aware execution (Plan J)
sagewai/autopilot/tool_scopes.py               Tool→scope registry, scopes_for_tools() (Plan K)
sagewai/autopilot/sealed_matcher.py            ProfileRecord, match_profile() — LRU tie-break (Plan K)
sagewai/autopilot/controller/sealed_runner.py  SealedToolRunner, JitHitlPendingError (Plan K)
sagewai/autopilot/errors.py                    NoWorkerAvailableError (Plan I)
apps/admin/components/autopilot/
  autopilot-fleet-panel.tsx     Fleet worker pool + per-step allocation (Plan I)
  autopilot-sandbox-panel.tsx   SandboxTier rows + override modal (Plan J)
  autopilot-sealed-panel.tsx    Sealed profile rows + JIT-HITL pill (Plan K)
  tier-badge.tsx                TierBadge component (Plan J)
```

**Admin routes added in Tier 3 (all under `/api/v1`):**
```
GET  /autopilot/fleet/workers                            Pool snapshot
GET  /autopilot/missions/{id}/fleet-allocation          Per-step matched workers
GET  /autopilot/missions/{id}/sandbox-allocation        Per-step SandboxTier
POST /autopilot/missions/{id}/sandbox-override          Downgrade-only tier override
GET  /autopilot/missions/{id}/sealed-allocation         Per-step Sealed profile match
POST /autopilot/missions/{id}/sealed-override           Manual Sealed profile assignment
```

**Tier 3 key invariants:**
- `FleetMissionAdapter.dispatch_step()` fails fast via `NoWorkerAvailableError` only when
  the pool has registered workers but none match; empty pool falls through to claim/timeout.
- `SandboxTier`: TRUSTED=0 < SANDBOXED=1 < UNTRUSTED=2. `is_downgrade(proposed, current)`
  returns True when `proposed > current` (more restrictive). Only downgrades accepted via admin.
- `SealedToolRunner`: raises `JitHitlPendingError` when tool requires scopes but no profile bound.
- `match_profile()` requires strict superset of scopes; LRU (least recently used) as tie-break.
- All three panels render in `MissionDetailView` below the trace, in order: Sandbox → Fleet → Sealed.

**Private server (`sagewai/sagewai-llm` — PROPRIETARY):**
- All production blueprints live there, NEVER in this OSS repo
- 7 API routes: generate, retrieve, publish, feed, telemetry, eval, quota
- HMAC auth, embedding-based retrieval, LiteLLM generation pipeline
- Telemetry gap miner for continuous library growth
- Landing page + API docs at the server root
- **NEVER copy code, prompts, blueprints, or design details from that
  repo to this one. Zero residue policy.**

**Key invariants:**
- ZERO production blueprints in this OSS repo — only `SYNTHETIC_` test fixtures
- The OSS design spec (`docs/superpowers/specs/`) has proprietary sections
  stripped (PR #99). Server-side architecture lives in the private repo.
- The `SagewaiLLMClient` is the only bridge: it calls the hosted service
  for blueprints. The platform works manually without the service.
- Autopilot admin routes require `sagewai_auth` cookie, missions are
  project-scoped via `X-Project-ID` header.

**Test counts:** ~757 autopilot tests + 60 private server tests = 817 total
(94 new tests from Plans I/J/K: 37 fleet + 20 sandbox + 37 sealed)

## LLM Harness (smart proxy for AI coding tools)

The harness is a separate product surface from agent orchestration: a
smart proxy that sits between AI coding tools (Claude Code, Cursor,
Copilot) and upstream LLM providers, classifying each request's
complexity and routing it to the cheapest model that can handle the
task — Opus → Haiku for simple edits, Opus → Sonnet for medium work,
Opus → Opus only when complexity warrants it. Per-team policies cap
spend; per-customer keys gate access; audit captures every decision.

**Architecture (`sagewai.harness.*`):**
```
sagewai/harness/classifier.py       Heuristic complexity scorer (token counts, keywords, code blocks)
sagewai/harness/router.py           Tier → model resolution
sagewai/harness/policy.py           Per-org/per-user routing rules
sagewai/harness/budget.py           Spend caps + downgrade/block on exceed
sagewai/harness/proxy.py            FastAPI proxy app (Anthropic + OpenAI compatible endpoints)
sagewai/harness/store.py            InMemoryHarnessStore (dev / single-process)
sagewai/harness/postgres_store.py   PostgresHarnessStore (production)
sagewai/harness/admin_api.py        REST CRUD for policies, keys, spend, audit, config
sagewai/harness/agent.py            HarnessingAgent integration for sagewai agents
sagewai/harness/middleware.py       harness_wrap() for adding tier-aware routing to existing agents
sagewai/harness/discovery.py        Auto-discovery of local backends (Ollama, LM Studio, vLLM)
apps/admin/app/harness/             Admin frontend: Dashboard, Policies, Keys, Analytics
```

**How it's wired into the admin:**
- `serve.py` mounts `create_harness_admin_router(...)` at `/api/v1/harness`
- 13 routes: `/policies` (CRUD), `/keys` (list/create/revoke), `/spend`, `/spend/breakdown`, `/audit`, `/config` (GET/PUT), `/test-classify`
- Uses `InMemoryHarnessStore` by default; swap to `PostgresHarnessStore` for multi-process production deployments (mirror the Sealed-iii.A pattern in serve.py — conditional on `SAGEWAI_DATABASE_URL`)
- See Examples 23 (`23_harness_proxy.py`) and 24 (`24_harness_agent.py`) for end-to-end demos

**Use it standalone:**
```bash
# Run as a proxy that Claude Code can call:
export ANTHROPIC_BASE_URL=http://localhost:8100/v1
export ANTHROPIC_API_KEY=sk-harness-<your-key>
python packages/sdk/sagewai/examples/23_harness_proxy.py
```

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
- `admin/serve.py` — complete FastAPI app factory; canonical registration point for ~160 admin endpoints across the core router plus mounted sub-routers (autopilot, sandbox, sealed, sealed-revocation, harness)
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

## Tool catalog (sub-project 1)

`packages/sdk/sagewai/tools/catalog/*.yaml` is the canonical tool catalog.
Every blueprint `tools_required:` string must have a matching `<id>.yaml`
here once the sub-project 2 batches land. Schema lives at
`catalog/_schema.json`; `registry.py` validates at import time and refuses
to load on any violation. Three seed entries shipped: `fetch_url` (kind:
sdk), `github` (kind: http, api_key), `filesystem_mcp` (kind: mcp).

Five executor modules at `sagewai/tools/executors/{sdk,http,mcp,cli,webhook}.py`
share one interface; `sagewai.tools.executors.get(kind)` returns the
matching `run` coroutine. `sagewai.tools.factory.build_callables()` adapts
each `CatalogEntry` into the autopilot `ToolCallable` shape so the
existing `ToolRunner` contract is preserved.

`sagewai.autopilot.tool_scopes.scopes_for_tools` delegates to
`registry.scopes_for` for catalogued names and falls back to the legacy
`_TOOL_SCOPES` dict otherwise. Sub-project 2 catalogues the remaining
legacy names tier by tier (no-auth → api_key → oauth2 → oauth2_webhook);
once the legacy dict is empty it can be deleted.

The inference-providers vault has been generalised to **connections** at
`/api/v1/admin/connections/*`. The old path returns 308 for one release.
Each record carries `kind: "inference" | "tool"`; the migration is
JSON-level inside `AdminStateFile` — no SQL. Admin UI: `/connections`
with `Inference` (current providers) + `Tools` (empty scaffold) tabs.

**Batch 1 (no-auth tier) landed:** 11 additional entries in five clusters:

- `text`: `diff_text`, `structured_write` (stdlib only, `TRUSTED`)
- `mission_state`: `record_result`, `progress_track`, `request_approval`
  (write through `set_mission_resolver` shim bound at autopilot startup;
  `Mission` gained 4 minimal stub methods to satisfy the duck-typed contract)
- `http_parsing`: `fetch_url` (re-homed from `autopilot/default_tools.py`,
  now deleted), `web_scrape`, `web_search`, `pdf_parse` (new deps:
  `readability-lxml==0.8.4.1`, `duckduckgo-search==8.1.1`, `pypdf==6.11.0`)
- `llm`: `content_translate` (complexity_hint=low), `quiz_generate`
  (complexity_hint=medium) via lazy `HarnessProxy.handle_request` with
  inline `_DirectLiteLLMBackend` (avoids needing a running proxy server)
- `notify` (channels: `log`, `event_bus`)

`packages/sdk/sagewai/admin/autopilot_default_tools.py` (the admin-side
`url→str` registry) is still in place — a separate cleanup PR
consolidates it after subsequent batches land.

**Batch 2a (api_key tier, comms cluster) landed:** 4 new entries +
Tools-tab CRUD:

- `post_to_slack` — Slack bot token; explicit `ok:false` Slack-API
  pattern handled (Slack returns 200 with `{ok:false}` on semantic
  failure).
- `discord_api` — `Authorization: Bot <token>` (not `Bearer`); 2000-char
  content cap validated pre-HTTP; 429 retry-once-then-degrade.
- `email_send` — Resend/SendGrid/Postmark via prefix auto-detect
  (`re_*`, `SG.*`, else explicit `EMAIL_PROVIDER`).
- `mailchimp_api` — datacenter parsed from the key suffix (`abc-us21`
  → `https://us21.api.mailchimp.com/3.0`); ops `add_subscriber` +
  `send_campaign`.

Catalog gained optional `setup.credential_fields` to drive the admin
Tools tab's dynamic form. The `sdk` executor now introspects each
builtin's signature: it passes `project_id` + `get_credentials` only
when the function accepts them (batch-1 builtins stay `payload`-only).
Multi-op sdk builtins read `_operation` from the payload — the SDK
executor re-injects it from the factory's `operation` kwarg.

The Tools tab in `/connections` is real CRUD: list, add via dynamic
modal, test via the catalog's `setup.test_endpoint`, delete. Backend at
`/api/v1/admin/connections/tools/*` plus `/tools/registry`. Tool
records live in the same isolated JSON store as inference records
(`~/.sagewai/inference-providers.json`) keyed by `kind: "tool"`.

**Batch 2b (api_key tier, CRM + Devtools/Data) landed:** 3 new entries
+ extended `github`:

- `hubspot_api` — Bearer `pat-na1-...`; ops: `search_contacts`,
  `create_contact`, `search_deals`, `account_info` (test endpoint).
  HubSpot's `/search` endpoints are POST with JSON body, not GET with
  query string.
- `greenhouse_api` — HTTP Basic with API key as username, **empty
  password** (Greenhouse Harvest convention). Locked in by a one-line
  regression test in `test_executors_http.py`.
- `maps_route` — `kind: sdk`; Google Maps Directions API via
  query-string `?key=...` (no header auth). Billing must be enabled
  even at the free tier or Google returns `REQUEST_DENIED`.
- `github` — extended with `create_issue`, `create_comment`,
  `create_pull_request`, `search_code`; tool-level scopes broadened
  to include `git.write`. Operators with read-only tokens registered
  under PR #347 will hit 403 on writes until they regenerate with
  broader `Issues: Write` + `Pull requests: Write` permissions.

No new top-level deps; no admin or frontend work — the Tools-tab CRUD
from batch 2a auto-discovers new tools via `/tools/registry`.

**Batch 2c (api_key tier, Payments cluster) landed:** 5 new entries:

- `stripe_api` — Bearer `sk_*`/`rk_*`; form-encoded POST bodies (new
  schema field `body_format: form|json` per http op). Ops:
  `create_payment_intent`, `retrieve_payment_intent`, `create_customer`,
  `list_customers`, `create_refund`, `account_info` (test).
- `adyen_api` — `X-API-Key` header. Test environment only in this
  batch; live URL prefix support is a follow-up PR (per-project
  base URL override in the http executor schema).
- `plaid_api` — `kind: sdk`. Dual-header auth (`PLAID-CLIENT-ID` +
  `PLAID-SECRET`). Base URL switches on `PLAID_ENV`
  (sandbox/development/production).
- `braintree_api` — `kind: sdk`. GraphQL endpoint + dynamic merchant_id
  in URL path. Basic auth `<public>:<private>`. Four credential fields
  including an env switch.
- `paypal_api` — `kind: sdk`. OAuth2 client-credentials grant with
  in-memory token cache keyed by `(project_id, client_id)`. 1-hour
  TTL with 60s safety buffer. Cache evicts on process restart;
  first post-restart request re-exchanges.

Schema change: optional `body_format: form|json` per http operation.
Default `json` matches all existing tools. Only Stripe ops use `form`.

Shared new scope: `payments.charge` declared on all five for "this
tool can move money."

**Batch 2d (api_key tier, Observability + Atlassian) landed:** 8 new
entries + Adyen live-URL fix:

- `amplitude_api` — `kind: sdk`. Amplitude HTTP V2 puts api_key in the
  request body; builtin injects it so blueprints stay credential-free.
- `opsgenie_api` — Bearer with `GenieKey ` prefix (not `Bearer `).
  Same auth pattern as Discord's `Bot `.
- `datadog_api` — `kind: sdk`. Dual-header auth (`DD-API-KEY` +
  `DD-APPLICATION-KEY`) plus region-switched base URL (US1/EU/etc.).
- `virustotal_api` — `x-apikey` header; 4 read ops; free-tier rate
  limit is restrictive (4/min, 500/day).
- `snyk_api` — Bearer with `Token ` prefix; every REST call requires
  `version` query parameter (`2024-10-15` or newer).
- `jira_api` — Basic auth `email:token` + site URL via the new
  `runtime_base_url_field` extension.
- `confluence_api` — same auth pattern as Jira; different paths.
- `compass_api` — `kind: sdk`. GraphQL endpoint; same Atlassian token
  works for Jira/Confluence/Compass.
- `adyen_api` (modified) — now reads `ADYEN_BASE_URL` from credentials
  for live operators (closes the batch-2c carry-forward).

Schema change: optional `runtime_base_url_field` on `exec.http` — names
a credential field that overrides `exec.http.base_url` at call time.
Default behaviour unchanged for entries that don't declare it.

New scopes: `observability.events`, `incident.management`,
`security.intel`, `security.scan`, `project.management`,
`knowledge.management`, `service.catalog`.

Atlassian shared-credentials UX note: jira_api, confluence_api,
compass_api all use the same `USERNAME` + `PASSWORD` fields. Operators
currently must register each separately. A future "shared credentials"
feature would let one registration serve multiple tools — not blocking.

**Batch 2e (api_key tier, E-commerce cluster) landed:** 3 new entries:

- `shopify` — `kind: sdk`. GraphQL Admin API; single endpoint
  `POST https://{store}/admin/api/{version}/graphql.json` with the
  `X-Shopify-Access-Token` header. Six ops (list/get + create_product,
  create_draft_order, adjust_inventory) via per-op GraphQL query
  templates. GraphQL returns HTTP 200 with a top-level `errors` array
  on failure — the builtin inspects the body and raises. API version
  defaults to `2025-10`, operator-overridable via `SHOPIFY_API_VERSION`.
- `magento` — `kind: http`. Magento 2 REST; Bearer token; store REST
  base URL operator-specific via `runtime_base_url_field: MAGENTO_BASE_URL`.
  Path-keyed lookups + creates only — Magento's `searchCriteria` query
  DSL doesn't map to the flat query-param model, so list/search ops are
  deferred.
- `joor_api` — `kind: http`. JOOR wholesale B2B; `x-api-key` header;
  fixed base URL `https://api.joor.com` (no runtime override). JOOR's
  `/v3/` paths are modeled from public API conventions — operators
  confirm exact paths against their JOOR partner agreement.

`amazon_sp_api` was deferred to its own dedicated PR (LWA token
exchange + regional routing + Restricted Data Tokens). New shared
scope: `ecommerce.write` on all three. No schema or executor changes —
batch 2e reuses `runtime_base_url_field` from 2d. No admin/frontend
work — the Tools-tab CRUD auto-discovers new tools via `/tools/registry`.

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

10. ~~`tests/sealed/test_vault_backend.py` errored in CI with
    `ModuleNotFoundError: hvac`~~ **FIXED** in PR #196 — `hvac` moved
    into the `[dependency-groups] test` section of
    `packages/sdk/pyproject.toml` so CI's `uv sync --group test`
    always pulls it. If you add a new backend that imports an
    optional-extra dep at module load, add the dep to the test group
    too (don't rely on the extra being explicitly installed in CI).

11. ~~`test_build_pool_raises_for_unsupported_strategy` failed
    `DID NOT RAISE NotImplementedError`~~ **FIXED** in PR #196 — the
    test used `PoolStrategy.EXTERNAL_MIN_REPLICAS`, which the K8s
    backend (PR #188) implemented; switched to `PROVIDER_MANAGED`
    (Lambda — still unimplemented). When new pool strategies land,
    this test must move to the next-still-unimplemented one.

12. ~~`test_redaction_perf_1mib_six_secrets` p99 budget too tight~~
    **FIXED** in PR #196 — bumped from 5ms to 30ms (CPython 3.10 on
    GitHub-hosted runners hits ~19ms; 3.13 ~3ms). Perf tests catch
    catastrophic regressions, not wall-clock SLAs on shared CPUs.
    If you add a new perf test, calibrate the budget on the slowest
    Python version × the `ubuntu-latest` runner, not on a fast laptop.

## Memory subsystem (Plan 1 — landed in PR #196)

`sagewai.memory` now exposes AgentCore-style extraction strategies and
per-mission branching:

- **`MemoryStrategy` Protocol** + three built-in strategies:
  `SemanticFactStrategy`, `PreferenceStrategy`, `SummaryStrategy`. Each
  takes a duck-typed LLM client (`acompletion(*, messages)`) and
  returns `list[ExtractedRecord]` per session.
- **`MemoryBranch(mission_id)`** — frozen+slots dataclass; `scoped(ns)`
  returns `f"{mission_id}/{ns}"`. `MemoryBranch.global_root()` is the
  `_global` sentinel.
- **`RAGEngine.ingest_turns(turns)`** — runs configured strategies and
  writes each `ExtractedRecord` to the vector store with branch-scoped
  namespace metadata. Two new optional kwargs on `__init__`:
  `strategies: list[MemoryStrategy] | None`, `branch: MemoryBranch | None`.
- **`Mission(memory=...)`** — accepts an optional `RAGEngine`; constructor
  stamps `memory._branch = MemoryBranch(mission_id=self.mission_id)`.
  Refuses to silently re-stamp an engine already scoped to a different
  mission (raises `ValueError`). Use one engine per mission.
- **Public re-exports** — all of the above are importable as
  `from sagewai.memory import MemoryBranch, SemanticFactStrategy, ...`.
- **Example 29** (`packages/sdk/sagewai/examples/29_memory_strategies.py`)
  illustrates the API surface (LLM client placeholders).
- **Known gap (issue #195):** `RAGEngine.retrieve` does not yet filter
  by branch — writes are namespaced, reads are not. Production
  `VectorMemory` retrieve must honour the branch prefix; tracked
  separately.

## Plan docs landed but not yet implemented (PR #196)

Five `docs/superpowers/plans/2026-04-30-*.md` from the AgentCore
comparison. Plan 1 (memory strategies) shipped; the remaining four are
ready to execute as separate PRs:

- `2026-04-30-gateway-semantic-tool-search.md` — `Embedder` + `ToolIndex`
  + `?q=&k=` on the discovery router.
- `2026-04-30-agentcore-runtime-backend.md` — adds `boto3` extra; new
  `SandboxBackend` impl alongside Docker/K8s.
- `2026-04-30-gateway-agentcore-federation.md` — `GatewayUpstream`
  protocol + `AgentCoreUpstream` (SigV4 transport stub; real wiring
  tracked as a follow-up issue when implemented).
- `2026-04-30-sealed-agentcore-identity-bridge.md` — `ProfileBackend`
  for AWS Bedrock AgentCore Identity workload tokens; mirrors the
  Vault backend pattern from PR #190.

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
- `docs/architecture/README.md` — canonical runtime architecture (4 docs: runtime topology, security tiers, execution modes, execution backends). Mirrored in user-facing tone at `apps/docs/app/docs/architecture/` (5 pages — overview + 4 chapters). Update both in the same PR.
- `apps/docs/CLOUDFLARE.md` — docs-site deploy model
- `packages/sdk/pyproject.toml` — Python deps, scripts, pytest markers
- `packages/sdk/README.md` — SDK API surface
- `brand/README.md` — canonical brand asset rules
- `.changeset/README.md` — release flow

## Docs-site MDX gotchas

When editing `apps/docs/app/**/*.mdx`:

- The MDX pipeline runs `remark-gfm` + `rehype-slug` (the latter as `['rehype-slug']` — string-name only; Turbopack rejects function references for serializability). Heading IDs auto-generate using github-slugger (em-dash spaces collapse to double hyphen, lowercase). Cross-page anchor links rely on this.
- Turbopack's MDX parser interprets `<digit` and `<lowercase` as JSX tag starts. Body prose containing `<100ms`, `<1s`, `<X minutes` etc. must be HTML-entity escaped (`&lt;100ms`). Code fences are fine.
- Anti-patterns sections use a numbered list with bold inline subjects (`1. **Subject.** explanation`), NOT h3 headings — keeps the list shape recognisable and prevents anti-patterns from drowning out higher-level structure in any future ToC.
