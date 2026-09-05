<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/sagewai_logo_dark.svg">
    <img alt="Sagewai" src="brand/sagewai_logo.svg" width="360">
  </picture>

  <h3>Safe, durable control for autonomous work.</h3>

  <p><strong>Sagewai turns a bounded WorkContract into a verified outcome with durable evidence, explicit gates, Codex implementation, and independent Claude review.</strong></p>

  [![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](./LICENSE)
  [![PyPI](https://img.shields.io/pypi/v/sagewai.svg)](https://pypi.org/project/sagewai/)
  [![Status: alpha](https://img.shields.io/badge/status-early%20(alpha)-orange)](#v10-status)
  [![ci-sdk](https://github.com/sagewai/platform/actions/workflows/ci-sdk.yml/badge.svg)](https://github.com/sagewai/platform/actions/workflows/ci-sdk.yml)
  [![ci-admin](https://github.com/sagewai/platform/actions/workflows/ci-admin.yml/badge.svg)](https://github.com/sagewai/platform/actions/workflows/ci-admin.yml)
  [![ci-docs](https://github.com/sagewai/platform/actions/workflows/ci-docs.yml/badge.svg)](https://github.com/sagewai/platform/actions/workflows/ci-docs.yml)

  <p>
    <a href="https://sagewai.ai">Website</a> ·
    <a href="https://docs.sagewai.ai">Docs</a> ·
    <a href="#quickstart">Quickstart</a> ·
    <a href="#repository-map">Repository map</a> ·
    <a href="#examples">Examples</a> ·
    <a href="https://sagewai.ai/commercial">Commercial license</a>
  </p>
</div>

---

## What is Sagewai

Sagewai is an open-source Work control plane. Give it a software goal or a GitHub issue and it persists a project-scoped WorkItem and WorkContract, compiles grounded evidence for each stage, lets Codex implement, lets Claude independently verify the result, and resumes from durable state after interruption. The lifecycle—not either model's chat history—is authoritative.

In practice that means you can:

- **Bound the work** with a versioned contract, explicit scope, acceptance criteria, assumptions, risk, and permissions.
- **Keep the evidence** as durable events, verification receipts, artifacts, and an Evidence Board that survives model sessions and process restarts.
- **Separate implementation from review**: Codex performs write stages; Claude performs read-only analysis and review; failed review enters a bounded repair loop.
- **Stop safely** at approval, blocked, or degraded-control states and surface them through the CLI and Work Control Console.
- **Execute locally by default**, or dispatch credential-free stages to compatible, project-scoped Fleet workers. Codex and Claude authentication remains on each worker.

The agent SDK, workflows, Autopilot, Sealed, Observatory, and Training Loop remain in the repository. They are retained capabilities, not the primary Work lifecycle described here. See the [evolution decisions](./docs/architecture/work-control-plane-evolution.md) for the current boundaries.

All AGPL-3.0. Install with `uv pip install sagewai` (or `pip install sagewai` inside a virtualenv), or clone the repo and build the full stack from source with `just stack-up`. Self-hostable on your own hardware.

> **Sagewai is early software.** The [v1.0 status](#v10-status) section is explicit about what ships today, what is experimental, and what is on the v1.1 roadmap — so you can decide what to rely on.

---

## Quickstart

Install the SDK into an isolated environment. The fastest, most reliable way is [uv](https://docs.astral.sh/uv/):

```bash
uv venv && uv pip install sagewai
uv run sagewai --version
```

Prefer pip? **Create a virtualenv first** — on macOS/Homebrew and many Linux distros a system-wide `pip install` is blocked with `error: externally-managed-environment`:

```bash
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install sagewai
sagewai --version
```

Run Work from a Git repository with a deterministic `just smoke` check; Sagewai also loads `AGENTS.md` when the repository provides it. Install and authenticate the native `codex` and `claude` CLIs on the machine doing the work; Sagewai uses their existing local authentication and does not collect those credentials. Verification always runs without network access in a disposable container, so select an immutable image containing this repository's locked test toolchain:

```bash
export SAGEWAI_WORK_VERIFICATION_IMAGE='registry.example/verifier@sha256:<digest>'

# Every command names its project. Use "global" only for org-global Work.
sagewai work --project my-project start "Add the accepted change and tests"

# Replace WORK_ID with the ID printed by start.
sagewai work --project my-project status WORK_ID
sagewai work --project my-project pending
sagewai work --project my-project resume WORK_ID
```

`start` persists the WorkItem, contract, evidence, stage receipts, and repository result. A successful uncontracted software run ends at a verified commit; it does not imply a deployment.

To see the coordinator work against small, deterministic targets, try the
[browser game and approval desk](./test-apps/) and follow the
[coordinator walkthrough](./docs/operations/work-coordinator.md).

For a GitHub issue, provide a token authorized for the target repository and pass the issue URL. The lifecycle opens a pull request and stops at the named merge gate. Use the exact IDs reported by `pending`:

```bash
export GITHUB_TOKEN=ghp_...
sagewai work --project my-project start https://github.com/OWNER/REPO/issues/123
sagewai work --project my-project pending
sagewai work --project my-project approve WORK_ID GATE_ID
sagewai work --project my-project resume WORK_ID
```

Local execution is the default. To use Fleet, start approved project-scoped workers from trusted checkouts that advertise `runtime.codex` or `runtime.claude`, then add `--execution fleet --fleet-org ORG_ID` before the subcommand. Native CLI authentication remains on those workers; the control plane sends no Codex or Claude credential. See the [Fleet Work example](./packages/sdk/sagewai/examples/fleet/README.md#native-work-operators).

`sagewai admin serve --port 8000` exposes the authenticated Work API. For the browser Work Control Console, run `just stack-up` from a source checkout and open http://localhost:3008. A fresh full-stack install still requires the first-time setup wizard; there are no default credentials and setup is never bypassed.

**Full stack with the web admin UI.** The admin UI ships as a container, so the full stack runs via Docker (or Podman). Sagewai is open source — **clone the repo and build the images from your own checkout** (nothing is pulled from a registry except the stock `postgres` + `redis`):

```bash
git clone https://github.com/sagewai/platform && cd platform
just stack-up                      # builds the backend + admin images from source, then starts
docker compose logs -f             # tail; admin UI at http://localhost:3008
just compose-down                  # stop
```

`just stack-up` is just `docker compose up -d --build`: it builds the backend image from `packages/sdk` (the repo-root context) and the Next.js admin image entirely from your checkout — only Docker is required, no host toolchain and no prebuilt artifact. (`postgres` + `redis` are pulled as stock upstream images.)

The **first** build compiles the Next.js admin and downloads the backend's Python dependencies, so it takes a few minutes — let it finish (don't cancel it; a cancelled build wastes the layer cache). After that, the backend's dependency install is cached separately from its source, so editing the code and rebuilding is a matter of seconds. Rebuild only the service you changed instead of the whole stack: `docker compose up -d --build backend` (or `admin`).

**Log in.** A fresh stack has no account yet, so create one first: open **http://localhost:3008/setup** and complete the short wizard (organization → admin account: name, email, password ≥ 8 characters). It signs you in automatically at the end; afterwards — or from another browser — sign in at **http://localhost:3008/login** with that email and password. There are no default credentials; the admin is whatever you set in the wizard.

**Locked out / forgot your password?** Email reset needs SMTP (not configured out of the box), so reset it locally instead — no email required:

```bash
sagewai admin reset-password                               # host install
docker compose exec backend sagewai admin reset-password   # Docker stack
```

It prompts for a new password and revokes existing sessions; sign back in at `/login`. To start over completely on a Docker stack, recreate the backend so the setup wizard runs again — `docker compose rm -sf backend && docker compose up -d backend` (Postgres data is kept; the file-backed admin config is reset).

> **Podman users:** if a build fails pulling a base image with `unauthorized: incorrect username or password`, you have a stale Docker Hub login — clear it with `podman logout docker.io` (public base images pull anonymously). If a bare image name like `redis:8-alpine` won't resolve, add `docker.io` to `unqualified-search-registries` in `~/.config/containers/registries.conf`.

Prebuilt `ghcr.io/sagewai/{backend,admin}` images are also published to GHCR on each tagged release if you'd rather pull than build (`docker pull ghcr.io/sagewai/backend:latest`) — but the bundled compose builds from source, which is the recommended path.

**Pre-release builds** live on TestPyPI (a normal `pip install sagewai` never picks these up):

```bash
uv pip install --index-strategy unsafe-best-match \
  --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple "sagewai==X.Y.ZrcN"
```

**From source** (contributors):

```bash
git clone https://github.com/sagewai/platform && cd platform
just bootstrap     # uv + pnpm + workspace sync
just dev-all       # backend API + admin UI together
```

---

## Persistence

Sagewai persists all state across restarts with no setup required. On first start it creates `~/.sagewai/` (override with `SAGEWAI_HOME`) and writes to three subdirectories:

| Path | What lives there |
|------|-----------------|
| `~/.sagewai/config/` | `admin-state.json`, `admin-ui-state.json` (UI-only), `connections.json` — human-readable config |
| `~/.sagewai/db/sagewai.db` | SQLite: sessions, runs, workflow checkpoints, analytics, vector learnings |
| `~/.sagewai/secrets/` | `master.key`, `profiles.json` — mode 0700 |

For production scale or multi-process deployments, set `SAGEWAI_DATABASE_URL=postgresql+asyncpg://…` and install the `sagewai[postgres]` extra. The same store layer runs on both backends with no code changes. See the [Persistence guide](https://docs.sagewai.ai/docs/guides/persistence) for the full layout, durability matrix, and migration steps.

---

## Repository map

Sagewai is a monorepo. The packages and apps:

| Path | What it is | Published as |
|---|---|---|
| [`packages/sdk`](./packages/sdk) | The `sagewai` Python package | `pip install sagewai` |
| [`apps/admin`](./apps/admin) | Next.js Work Control Console and retained admin surfaces | `ghcr.io/sagewai/admin` |
| [`apps/backend`](./apps/backend) | Docker image wrapping the SDK (`sagewai admin serve`) | `ghcr.io/sagewai/backend` |
| [`apps/docs`](./apps/docs) | Docs site (Next.js, deployed to Cloudflare) | [docs.sagewai.ai](https://docs.sagewai.ai) |
| [`apps/vscode-extension`](./apps/vscode-extension) | VS Code extension | VS Code Marketplace |

Inside the SDK — [`packages/sdk/sagewai/`](./packages/sdk/sagewai) — the subpackages you'll read most:

| Subpackage | Responsibility |
|---|---|
| `cli` | The `sagewai` command-line entrypoints |
| `work` | Durable Work kernel, evidence, operator runtimes, and profiles |
| `engines` | Agent runtimes (`UniversalAgent`, workflows) |
| `autopilot` | Goal → agent-graph planning and missions |
| `fleet` | Distributed workers, dispatch, enrollment |
| `sandbox` | Isolated execution backends (Docker, Kubernetes) |
| `sealed` | Workload identity, secret backends, redaction |
| `gateway` | Multi-model LLM proxy and routing |
| `harness` | Cost-aware proxy for AI coding tools |
| `mcp` | Model Context Protocol client and server |
| `connections` | External protocols and credential backends |
| `memory` | Typed memory, RAG, extraction strategies |
| `safety` | Guardrails, PII redaction, content filters |
| `examples` | Runnable end-to-end examples |

All packages release together on one `vX.Y.Z` tag — see [`.changeset/README.md`](./.changeset/README.md).

---

## Developing

```bash
git clone git@github.com:sagewai/platform.git
cd platform
./scripts/bootstrap.sh          # installs uv + pnpm, syncs the workspace
```

Common tasks (run `just` for the full list):

| Recipe | What it runs |
|---|---|
| `just smoke` | Exercises the core software Work lifecycle with safe fakes; no LLM calls or external service calls |
| `just test` | Full SDK unit suite |
| `just dev-all` | Backend + admin UI on localhost (no containers) |
| `just stack-up` | Full container stack, building backend+admin images locally (postgres + redis + backend + admin) |
| `just build` | sdk wheel + admin + docs + vscode builds |

Package-scoped variants exist for targeted work: `just sdk-test`, `just admin-dev`, `just docs-dev`, and more. See [DEVELOPMENT.md](./DEVELOPMENT.md) for prerequisites and [CONTRIBUTING.md](./CONTRIBUTING.md) for the PR flow.

---

## Examples

Every example is a complete, runnable file in [`packages/sdk/sagewai/examples/`](./packages/sdk/sagewai/examples/). Start here:

- [`01_hello_agent.py`](./packages/sdk/sagewai/examples/01_hello_agent.py) — a minimal agent in five lines.
- [`02_tool_agent.py`](./packages/sdk/sagewai/examples/02_tool_agent.py) — give an agent a Python function as a tool with `@tool`.
- [`05_workflow.py`](./packages/sdk/sagewai/examples/05_workflow.py) — chain agents into a multi-stage workflow.
- [`06_guardrails.py`](./packages/sdk/sagewai/examples/06_guardrails.py) — PII redaction, content filters, and budget caps.
- [`20_fleet_workers.py`](./packages/sdk/sagewai/examples/20_fleet_workers.py) — run agents across a worker fleet with a dispatcher.

The full set:

| # | File | What it shows |
|---|---|---|
| 01 | [`01_hello_agent.py`](./packages/sdk/sagewai/examples/01_hello_agent.py) | Minimal agent in 5 lines |
| 02 | [`02_tool_agent.py`](./packages/sdk/sagewai/examples/02_tool_agent.py) | `@tool` decorator, function calling |
| 03 | [`03_multi_model.py`](./packages/sdk/sagewai/examples/03_multi_model.py) | Swap models per agent (GPT, Claude, Gemini, local) |
| 04 | [`04_memory_agent.py`](./packages/sdk/sagewai/examples/04_memory_agent.py) | Persistent memory + knowledge graph |
| 05 | [`05_workflow.py`](./packages/sdk/sagewai/examples/05_workflow.py) | Multi-stage agent workflows |
| 06 | [`06_guardrails.py`](./packages/sdk/sagewai/examples/06_guardrails.py) | PII redaction, content filters, budget caps |
| 07 | [`07_mcp_tools.py`](./packages/sdk/sagewai/examples/07_mcp_tools.py) | Expose agent tools as an MCP server |
| 08 | [`08_directives.py`](./packages/sdk/sagewai/examples/08_directives.py) | `@context`, `@memory`, `@agent:name()` directive syntax |
| 09 | [`09_proxy_claude_code.py`](./packages/sdk/sagewai/examples/09_proxy_claude_code.py) | Route Claude Code through the harness |
| 10 | [`10_proxy_cursor.py`](./packages/sdk/sagewai/examples/10_proxy_cursor.py) | Route Cursor through the harness |
| 11 | [`11_proxy_codex.py`](./packages/sdk/sagewai/examples/11_proxy_codex.py) | Route OpenAI Codex / other clients through the harness |
| 12 | [`12_budget_enforcement.py`](./packages/sdk/sagewai/examples/12_budget_enforcement.py) | Per-agent and per-workspace spend caps |
| 13 | [`13_model_routing.py`](./packages/sdk/sagewai/examples/13_model_routing.py) | Complexity-based routing (cheap model → expensive model) |
| 14 | [`14_register_agent.py`](./packages/sdk/sagewai/examples/14_register_agent.py) | Register an agent with the admin backend |
| 15 | [`15_discover_agents.py`](./packages/sdk/sagewai/examples/15_discover_agents.py) | Discover and invoke remote agents |
| 16 | [`16_agent_governance.py`](./packages/sdk/sagewai/examples/16_agent_governance.py) | Approval gates and human-in-the-loop |
| 17 | [`17_unsloth_finetune.py`](./packages/sdk/sagewai/examples/17_unsloth_finetune.py) | Fine-tune a local model with Unsloth |
| 18 | [`18_local_llm_routing.py`](./packages/sdk/sagewai/examples/18_local_llm_routing.py) | Auto-discover Ollama models, route requests locally |
| 19 | [`19_domain_model.py`](./packages/sdk/sagewai/examples/19_domain_model.py) | Train a domain-specific model end-to-end |
| 20 | [`20_fleet_workers.py`](./packages/sdk/sagewai/examples/20_fleet_workers.py) | Distributed worker fleet with dispatcher |
| 21 | [`21_full_stack.py`](./packages/sdk/sagewai/examples/21_full_stack.py) | Backend + admin UI + agents end-to-end |
| 23 | [`23_harness_proxy.py`](./packages/sdk/sagewai/examples/23_harness_proxy.py) | LLM harness as proxy for all AI calls |
| 24 | [`24_harness_agent.py`](./packages/sdk/sagewai/examples/24_harness_agent.py) | Agents running under the harness with a full audit trail |

A guided video series is in production — see the [video guide](https://docs.sagewai.ai/docs/guides/video-tutorials). Every episode maps to an example above.

---

## v1.0 status

Sagewai is early software. Here is what is real today, what is experimental, and what is coming, so you know what to rely on.

**Shipped**
- **SDK** — agents, `@tool` calling, 100+ models via LiteLLM, typed memory, guardrails, and multi-stage workflows.
- **Task coordinator** — one durable Task per goal: intake and clarification, a planned cycle
  of Work, gates a human decides, a spend ledger, schedules, and the console at `/board`.
- **Fleet** — capability-based dispatch with project isolation; **Docker** (default) and **Kubernetes** sandbox backends.
- **Observatory** — OpenTelemetry traces, metrics, and per-model / per-team cost tracking.
- **Training Loop — capture** (the Curator) and run-level execution modes.
- **Sealed** — the workload-identity model, an external secret backend (HashiCorp Vault), and admin profile/secret controls.

**Experimental** — built and tested, not yet wired into the default run path
- Automatic **healing** — the engine surfaces recommended actions; it does not yet act on them.
- Sealed **runtime enforcement** — live secret injection, redaction, per-key ACL, and mid-run revocation.

**Roadmap → v1.1** (target ~2026-07)
- The **closed cost-down loop** — fine-tune a promoted local model and deploy it via Ollama.
- Per-step execution modes; branch-filtered memory retrieval; durable Fleet persistence.
- Additional sandbox backends (e.g. AWS Lambda).

We would rather tell you this plainly than have you find it out in production.

---

## Sagewai vs. the alternatives

| | Sagewai | LangChain / LangGraph | CrewAI | OpenAI Agents SDK |
|---|---|---|---|---|
| Self-hostable backend + admin UI | ✅ | ❌ (library only) | ❌ | ❌ |
| Worker fleet on your own hardware | ✅ | ❌ | ❌ | ❌ |
| Built-in budget, guardrails, cost analytics | ✅ | Partial (LangSmith = SaaS) | ❌ | ❌ |
| Any model, any provider | ✅ | ✅ | ✅ | OpenAI-first |
| One-command full stack from source (`just stack-up`) | ✅ | ❌ | ❌ | ❌ |
| License for embedding in a proprietary product | Commercial | MIT | MIT | Apache |

Sagewai is the answer to "I want a self-hosted agent platform with the admin UI, the worker fleet, and no vendor lock-in."

---

## Client wrappers

Thin, language-idiomatic clients talk to a running Sagewai backend over HTTP, for teams whose stack isn't Python: TypeScript/JavaScript, Go, Rust, Java, Kotlin, C#/.NET, Scala, Ruby, PHP, Swift, C/C++, Dart, Flutter, React Native, Elixir, and Perl — plus a standalone Python client. See the [client wrappers guide](https://docs.sagewai.ai/docs/guides/client-wrappers). The monorepo `packages/sdk` remains the primary Python surface for authoring agents.

---

## Contributing

Pull requests are welcome for bug fixes, docs, and small features. For larger changes, open a GitHub Discussion first so we can align on scope before you write code.

By contributing you agree to the [Contributor License Agreement](./CLA.md): you keep copyright of your contribution while granting the maintainer a broad license to use and relicense it under AGPL-3.0 and commercial terms — the same dual-license model used by Ghost, Cal.com, and MariaDB.

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for coding conventions and the PR flow, and [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md) for community expectations.

## Roadmap

The roadmap lives on the public [Sagewai Roadmap board](https://github.com/orgs/sagewai/projects) (read-only for visitors). Anything not on the board is not planned.

## Governance

Sagewai uses a closed-governance model: branch protection, CODEOWNERS review, and a maintainer-gated issue tracker. Community input flows through GitHub Discussions; Issues are the maintainer team's worklist. This is the same model Bun, Deno, and Tauri use for commercial-OSS projects.

## License

Sagewai is licensed under the **GNU Affero General Public License v3.0 or later** ([LICENSE](./LICENSE)).

A **commercial license** is available for organizations that need to:
- embed Sagewai in proprietary software without AGPL-3.0 obligations,
- offer Sagewai as a managed service (SaaS) without sharing platform code, or
- white-label or remove Sagewai branding.

See [`COMMERCIAL-LICENSE.md`](./COMMERCIAL-LICENSE.md) and contact [licensing@sagewai.ai](mailto:licensing@sagewai.ai) for terms. For the rationale behind the dual-license model, see [`LICENSE_FAQ.md`](./LICENSE_FAQ.md).

---

<div align="center">
  <sub>Built in Berlin. Sagewai is a trademark of its maintainer.</sub>
</div>
