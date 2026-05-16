# Sagewai

[![PyPI](https://img.shields.io/pypi/v/sagewai)](https://pypi.org/project/sagewai/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](https://github.com/sagewai/platform/blob/main/LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/sagewai)](https://pypi.org/project/sagewai/)

## The factory that runs itself.

**Sagewai is the autonomous agent platform: describe the goal, we design the agents, run them in production, and fine-tune local models so every run gets cheaper.**

> *Five pillars hold up the platform; one spine runs through all of them — that's what makes the agent platform safe to give a credit card.*

## Built for the senior engineer who has one quarter to ship AI

A SaaS engineer told to "add AI this quarter" with a tight budget and a CFO three months out. The platform walks them through the whole arc:

- **Q1: ship the AI feature** — SDK, tools, memory, workflows. Deadline met.
- **Q2: explain the cost** — Observatory breaks the bill down by model, team, and feature.
- **Q3: cost-down** — Training loop captures successful runs, fine-tunes a local SLM, deploys via Ollama. End-to-end under $5.
- **Q4: the strategic question** — "If Anthropic raised prices 10×, how badly would we hurt?" Answer: "We'd be fine, we already have our own model."

## Five pillars

| Pillar | What it does |
|--------|-------------|
| **SDK** | Python-native agent runtime — multi-model providers, tools via MCP gateway, typed memory with extraction strategies and per-mission branching and checkpoint save/restore, guardrails, and LLM proxy in one import |
| **Autopilot** | State the goal in plain English. Autopilot designs the agent graph, extracts the slots, previews the plan, runs the mission, and heals on failure. The headline experience of the platform |
| **Fleet** | Distributed workers with capability-based dispatch, project isolation, enrollment keys, and isolated execution sandboxes (image families, Kubernetes backend, AgentCore-runtime backend, pooling). Run agents on your hardware, in your network |
| **Observatory** | OpenTelemetry tracing, VictoriaMetrics metrics, Grafana dashboards, cost tracking, audit trail. Your AI source of truth |
| **Training Loop — from juggernauts to your own model.** | Start with Opus or GPT-5. Capture their answers as training data via the Curator. Fine-tune your own SLM on free Colab CUDA, on $0.30/hr Spheron bare-metal, on serverless Modal, or on whatever GPU you can rent. Deploy locally via Ollama. Cost-down isn't an optimisation — it's an exit clause. |

## One spine — Sealed

Defense-in-depth security across all five pillars: per-CLI workload identity, externalised secret backends with JIT credentials, prompt + tool-output redaction at the RPC boundary, replay safety, per-CLI ACL, JIT-HITL callbacks, reactive directives. Five phases, twelve specs — the security model agent platforms have been ignoring.

## Quick start

```bash
pip install sagewai
```

```python
import asyncio
from sagewai import UniversalAgent

agent = UniversalAgent(name="hello", model="gpt-4o-mini")
print(asyncio.run(agent.chat("What is Sagewai?")))
```

Three lines to your first agent. Works with GPT-4o, Claude, Gemini, Mistral, Ollama, and 100+ models.

## Install extras

| Extra | What it adds |
|-------|-------------|
| `sagewai[memory]` | Milvus, NebulaGraph, Docling, tiktoken |
| `sagewai[intelligence]` | Embeddings, entity extraction, language detection |
| `sagewai[postgres]` | asyncpg, SQLAlchemy async, Alembic |
| `sagewai[fastapi]` | FastAPI + SSE support |
| `sagewai[prometheus]` | Prometheus metrics exporter |
| `sagewai[storage]` | S3 (boto3) and GCS archival backends |
| `sagewai[all]` | Everything above |

## Examples

Examples organised under the five-pillar architecture (see [`sagewai/examples/`](sagewai/examples/)):

- **SDK** — `01_hello_agent.py` through `08_directives.py`: agents, tools, multi-model, memory strategies, workflows, guardrails, MCP, directives (`@context`, `@memory`, `@agent`, `@transform`, `/tool`).
- **Autopilot** — `09_*_autopilot.py` group: goal-driven missions, agent-graph design, slot extraction.
- **Fleet** — `26_fleet_demo.py`: workers, capability dispatch, project scoping, sandbox execution.
- **Observatory** — examples emit OTel spans and Prometheus metrics consumed by the local Grafana stack.
- **Training Loop** — `25_training_pipeline.py`: collect, curate, export Alpaca/ShareGPT, fine-tune with Unsloth.
- **Transform directive** — `50_incident_knowledge_graph.py`: `@transform(graphify, …)` to distil incident transcripts into `GraphMemory` across runs. `51_big_input_small_model.py`: compress a large document with `@transform(summarize, …)` so a local model can answer questions about it; demonstrates custom transform ops via `transform.register(…)`.

## CLI

```bash
sagewai init my-project              # scaffold a new project
sagewai doctor                       # check environment health
sagewai agent run my_agent.yaml      # run an agent from config
sagewai admin serve --port 8000      # start the admin UI + API
```

## Documentation

- [docs.sagewai.ai](https://docs.sagewai.ai) — full documentation
- [Getting Started](https://docs.sagewai.ai/docs/getting-started) — quickstart guide
- [Architecture](https://docs.sagewai.ai/docs/architecture) — runtime topology, security tiers, execution modes, execution backends

## Contributing

See [CONTRIBUTING.md](https://github.com/sagewai/platform/blob/main/CONTRIBUTING.md) for development setup, code style, and PR process.

## License

AGPL-3.0-or-later — see [LICENSE](https://github.com/sagewai/platform/blob/main/LICENSE). Commercial licenses available for organisations that need an alternative to AGPL. See [COMMERCIAL-LICENSE.md](https://github.com/sagewai/platform/blob/main/COMMERCIAL-LICENSE.md) for details.

Built by [Ali Arda Diri](https://github.com/sagewai).
