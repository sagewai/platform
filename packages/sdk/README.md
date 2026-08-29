# Sagewai

[![PyPI](https://img.shields.io/pypi/v/sagewai)](https://pypi.org/project/sagewai/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](https://github.com/sagewai/platform/blob/main/LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/sagewai)](https://pypi.org/project/sagewai/)

## Safe, durable control for autonomous work.

**Sagewai turns a bounded WorkContract into a verified outcome with durable evidence, explicit gates, Codex implementation, and independent Claude review.**

The Work control plane is the primary operating path. It persists project-scoped lifecycle state and evidence independently of model sessions, runs locally by default, and can dispatch credential-free stages to compatible Fleet workers.

> **Sagewai is early software.** The sections below are explicit about what ships today, what is experimental, and what is on the v1.1 roadmap — so you know what to rely on.

## Quick start

Install into an isolated environment. [uv](https://docs.astral.sh/uv/) is fastest; with plain pip, **create a virtualenv first** — a system-wide `pip install` is blocked on macOS/Homebrew and many Linux distros with `error: externally-managed-environment`:

```bash
uv venv && uv pip install sagewai
# or:  python3 -m venv .venv && source .venv/bin/activate && pip install sagewai
sagewai --version
```

Run these commands from the Git repository being changed. Install and authenticate the native `codex` and `claude` CLIs locally, and select a digest-pinned verification image containing the repository's locked `just smoke` toolchain:

```bash
export SAGEWAI_WORK_VERIFICATION_IMAGE='registry.example/verifier@sha256:<digest>'

sagewai work --project my-project start "Add the accepted change and tests"
sagewai work --project my-project status WORK_ID
sagewai work --project my-project pending
sagewai work --project my-project resume WORK_ID
```

Every Work command requires an exact project slug or `global`. Sagewai persists the WorkItem, WorkContract, evidence, stage receipts, and verified repository result. Codex writes; Claude performs independent read-only analysis and review. Their native authentication remains on the execution machine.

For a GitHub issue, export a `GITHUB_TOKEN` authorized for the target repository, pass the issue URL to `start`, then use the exact Work and gate IDs surfaced by `pending`:

```bash
sagewai work --project my-project start https://github.com/OWNER/REPO/issues/123
sagewai work --project my-project pending
sagewai work --project my-project approve WORK_ID GATE_ID
sagewai work --project my-project resume WORK_ID
```

Local execution is the default. Fleet execution is opt-in with `sagewai work --project my-project --execution fleet --fleet-org ORG_ID ...`; approved workers advertise `runtime.codex` or `runtime.claude` and use their own local CLI authentication. See the [native Work worker guide](sagewai/examples/fleet/README.md#native-work-operators).

A `pip install sagewai` includes the CLI and authenticated Work API (`sagewai admin serve`). The browser Work Control Console ships in the separate admin container; fresh full-stack installations must complete the first-time setup wizard and have no default credentials. A verified software outcome does not imply deployment unless its contract explicitly selects a delivery extension.

## Install extras

The base install already includes the CLI, the admin API server (FastAPI + uvicorn), and the connection protocols — `sagewai admin serve` works with no extras. Add extras for optional capabilities:

| Extra | What it adds |
|-------|-------------|
| `sagewai[memory]` | Milvus, NebulaGraph, Docling, tiktoken |
| `sagewai[intelligence]` | Embeddings, entity extraction, language detection |
| `sagewai[postgres]` | asyncpg, SQLAlchemy async, Alembic |
| `sagewai[prometheus]` | Prometheus metrics exporter |
| `sagewai[storage]` | S3 (boto3) and GCS archival backends |
| `sagewai[all]` | Everything above |

## Released vs. pre-release versions

- **Stable:** `pip install sagewai` installs the latest release from PyPI. pip never selects a pre-release/dev build unless you ask for it explicitly.
- **Release candidates** are published to TestPyPI:
  ```bash
  pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple "sagewai==X.Y.ZrcN"
  # with uv, add: --index-strategy unsafe-best-match
  ```
  The `--extra-index-url` pulls dependencies from real PyPI while the package itself comes from TestPyPI.

## Install from source

```bash
git clone https://github.com/sagewai/platform && cd platform
just bootstrap                 # uv + pnpm + workspace sync
# or just the SDK, editable, inside an activated venv:
uv pip install -e packages/sdk
```

## What you can build with it

The Work control plane is the primary product surface. Existing SDK subsystems are retained where useful and are not compatibility promises:

- **Work** — durable WorkItems and contracts, Evidence Board retrieval, disposable Codex/Claude operators, deterministic verification, independent review/repair, GitHub gates, pending attention, and restart recovery.
- **Fleet** — capability-based, project-scoped execution for Work stages on machines whose Codex and Claude credentials stay local; standalone agent-task dispatch also remains available.
- **Admin and backend** — the Work Control Console and canonical project-scoped API, alongside retained administration surfaces.
- **Agent SDK, workflows, Autopilot, Sealed, Observatory, and Training Loop** — retained and tested capabilities, parked as the primary getting-started narrative while Work is the product center.

## Examples

Every example is a complete, runnable file in [`sagewai/examples/`](sagewai/examples/), grouped by product.

**SDK**
- [`01_hello_agent.py`](sagewai/examples/01_hello_agent.py) — a minimal agent in a few lines.
- [`02_tool_agent.py`](sagewai/examples/02_tool_agent.py) — give an agent a Python function as a tool with `@tool`.
- [`03_multi_model.py`](sagewai/examples/03_multi_model.py) — swap models per agent (GPT, Claude, Gemini, local).
- [`04_memory_agent.py`](sagewai/examples/04_memory_agent.py) — persistent typed memory and a knowledge graph.
- [`05_workflow.py`](sagewai/examples/05_workflow.py) — chain agents into a multi-stage workflow.
- [`06_guardrails.py`](sagewai/examples/06_guardrails.py) — PII redaction, content filters, and budget caps.
- [`07_mcp_tools.py`](sagewai/examples/07_mcp_tools.py) — expose agent tools as an MCP server.
- [`08_directives.py`](sagewai/examples/08_directives.py) — `@context`, `@memory`, and `@agent` directive syntax.

**Autopilot**
- [`28_autopilot_quickstart.py`](sagewai/examples/28_autopilot_quickstart.py) — describe a goal; Autopilot designs and runs the agent graph.
- [`35_autopilot_hosted_service.py`](sagewai/examples/35_autopilot_hosted_service.py) — drive Autopilot missions behind a hosted service.

**Fleet**
- [`20_fleet_workers.py`](sagewai/examples/20_fleet_workers.py) — run agents across a worker fleet with a dispatcher.
- [`26_fleet_scoped_dispatch.py`](sagewai/examples/26_fleet_scoped_dispatch.py) — capability-based dispatch with project scoping.
- [`33_fleet_sealed_integration.py`](sagewai/examples/33_fleet_sealed_integration.py) — workers that resolve secrets through Sealed identity profiles.

**Observatory**
- [`34_observatory_cost_tracking.py`](sagewai/examples/34_observatory_cost_tracking.py) — per-model / per-team cost tracking from run telemetry.
- [`43_observatory_live.py`](sagewai/examples/43_observatory_live.py) — emit OTel spans and metrics into the local Grafana stack.

**Training Loop**
- [`25_training_data_pipeline.py`](sagewai/examples/25_training_data_pipeline.py) — capture and curate production runs into Alpaca/ShareGPT training data.
- [`38_unsloth_finetune.py`](sagewai/examples/38_unsloth_finetune.py) — fine-tune a local model with Unsloth.
- [`36_autopilot_training_loop.py`](sagewai/examples/36_autopilot_training_loop.py) — an offline walkthrough of the full capture → fine-tune → deploy loop (v1.1 roadmap).

## Persistence

Sagewai persists all state across restarts with no setup required. On first start it creates `~/.sagewai/` (override with `SAGEWAI_HOME`):

| Path | What lives there |
|------|-----------------|
| `~/.sagewai/config/` | `admin-state.json`, `connections.json` — human-readable, durable |
| `~/.sagewai/db/sagewai.db` | SQLite: sessions, runs, workflow checkpoints, analytics, vector learnings |
| `~/.sagewai/secrets/` | `master.key`, `profiles.json` — mode 0700 |

For production scale or multi-process deployments, set `SAGEWAI_DATABASE_URL=postgresql+asyncpg://…` and install `sagewai[postgres]`. See the [Persistence guide](https://docs.sagewai.ai/docs/guides/persistence) for details.

## CLI

```bash
sagewai work --project my-project start "accepted outcome"
sagewai work --project my-project status WORK_ID
sagewai work --project my-project resume WORK_ID
sagewai work --project my-project pending
sagewai work --project my-project approve WORK_ID GATE_ID
sagewai admin serve --port 8000      # authenticated Work API
```

## Documentation

- [docs.sagewai.ai](https://docs.sagewai.ai) — full documentation
- [Getting Started](https://docs.sagewai.ai/docs/get-started/quickstart) — quickstart guide
- [Architecture](https://docs.sagewai.ai/docs/architecture) — runtime topology, security model, execution modes, execution backends

## Contributing

See [CONTRIBUTING.md](https://github.com/sagewai/platform/blob/main/CONTRIBUTING.md) for development setup, code style, and PR process.

## License

AGPL-3.0-or-later — see [LICENSE](https://github.com/sagewai/platform/blob/main/LICENSE). Commercial licenses available for organisations that need an alternative to AGPL. See [COMMERCIAL-LICENSE.md](https://github.com/sagewai/platform/blob/main/COMMERCIAL-LICENSE.md) for details.

Built in Berlin.
