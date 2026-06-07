# Sagewai

[![PyPI](https://img.shields.io/pypi/v/sagewai)](https://pypi.org/project/sagewai/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](https://github.com/sagewai/platform/blob/main/LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/sagewai)](https://pypi.org/project/sagewai/)

## The factory that runs itself.

**Sagewai is the autonomous agent platform: describe the goal, we design the agents, run them in production, and fine-tune local models so every run gets cheaper.**

Build your agent with the SDK. Hand it goals with Autopilot. Run them across teams with Fleet. Keep every secret scoped with Sealed. Watch every dollar with Observatory. Then own the model with the Training Loop.

> **Sagewai is early software.** The sections below are explicit about what ships today, what is experimental, and what is on the v1.1 roadmap — so you know what to rely on.

## Quick start

Install into an isolated environment. [uv](https://docs.astral.sh/uv/) is fastest; with plain pip, **create a virtualenv first** — a system-wide `pip install` is blocked on macOS/Homebrew and many Linux distros with `error: externally-managed-environment`:

```bash
uv venv && uv pip install sagewai
# or:  python3 -m venv .venv && source .venv/bin/activate && pip install sagewai
sagewai --version
```

```python
import asyncio
from sagewai.engines.universal import UniversalAgent

# Set an API key for your provider (OPENAI_API_KEY / ANTHROPIC_API_KEY), or use model="ollama/llama3.2".
agent = UniversalAgent(name="hello", model="gpt-4o-mini")
print(asyncio.run(agent.chat("What is Sagewai?")))
```

One interface reaches 100+ models — OpenAI, Anthropic, Google, Mistral, and local Ollama via LiteLLM — so you are not locked to a provider.

A `pip install sagewai` includes the **CLI** and the admin **API** (`sagewai admin serve` → `http://localhost:8000`, interactive docs at `/docs`). The web admin **UI** is a separate container image — run the full stack with `docker compose up` (see the [repository](https://github.com/sagewai/platform)).

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

Sagewai is one platform with several products. Here is the honest shape of each today:

- **SDK** — write an agent in a few lines of Python: multi-model providers, tools over MCP, typed memory, and guardrails in one import. Ships today.
- **Autopilot** — describe a goal in plain English and it designs and runs the agent graph for you. Linear plans run end-to-end today; branched/conditional plans and automatic healing (recommendations only, not yet acted on) are in progress.
- **Fleet** — run agents across your own machines with capability-based dispatch and project isolation, in Docker (default) or Kubernetes sandboxes. Ships today; durable persistence is on the roadmap.
- **Sealed** — keep secrets out of your agents with per-workload identity, an external secret backend (HashiCorp Vault), and admin profile/secret controls. The identity model, the Vault backend, and the admin controls ship today; runtime enforcement — live injection, redaction, per-key ACL, mid-run revocation — is experimental and maturing.
- **Observatory** — see what a run costs with OpenTelemetry traces, metrics, and a per-model / per-team spend breakdown. Ships today.
- **Training Loop** — capture good production runs and fine-tune a local model from them. v1.0 ships run capture (the Curator); the closed capture → fine-tune → deploy-via-Ollama loop is on the v1.1 roadmap.

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
sagewai init my-project              # scaffold a new project
sagewai doctor                       # check environment health
sagewai agent run my_agent.yaml      # run an agent from config
sagewai admin serve --port 8000      # start the admin API (web UI ships separately via Docker)
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
