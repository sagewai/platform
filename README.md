# Sagewai

![PyPI](https://img.shields.io/pypi/v/sagewai)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Python](https://img.shields.io/pypi/pyversions/sagewai)

**Agent Infrastructure You Own**

The open-source platform for AI agents that run, remember, and report. Build agents in Python. Operate them from a dashboard. Own everything.

## The 5 Pillars

| Pillar | Description |
|--------|-------------|
| **SDK** | Build agents with multi-model support, tools, memory, guardrails, and durable workflows |
| **Registry** | Store, version, discover, and govern AI agents across your organization |
| **Harness** | Proxy, route, and budget-control all LLM access (Claude Code, Cursor, Codex) |
| **Observatory** | Source of truth for all AI expenditure -- costs, tokens, audit trails, metrics |
| **Training** | Fine-tune domain LLMs with Unsloth, serve locally at $0/token |

## Quick Start

```bash
pip install sagewai
```

```python
import asyncio
from sagewai import UniversalAgent

agent = UniversalAgent(name="hello", model="gpt-4o-mini")
print(asyncio.run(agent.chat("What is Sagewai?")))
```

Three lines to create an agent. Works with GPT-4o, Claude, Gemini, Mistral, Ollama, and 100+ models via LiteLLM.

## Install Extras

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

21 progressive examples organized by pillar:

**SDK** (01-08): Hello agent, tools, multi-model, memory, workflows, guardrails, MCP, directives

**Harness** (09-13): Claude Code proxy, Cursor proxy, Codex proxy, budget enforcement, model routing

**Registry** (14-16): Register agents, discover agents, agent governance

**Training** (17-19): Unsloth fine-tune, local LLM routing, domain models

**Enterprise** (20-21): Fleet workers, full stack setup

See [`sagewai/examples/`](sagewai/examples/) for all examples.

## CLI

```bash
sagewai init my-project              # scaffold a new project
sagewai doctor                       # check environment health
sagewai agent run my_agent.yaml      # run an agent from config
sagewai harness start                # start the LLM proxy
```

## Documentation

- [docs.sagewai.ai](https://docs.sagewai.ai) -- full documentation
- [Getting Started](https://docs.sagewai.ai/docs/getting-started) -- quickstart guide
- [LLM Harness Guide](https://docs.sagewai.ai/docs/guides/harness) -- govern AI coding tool costs

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and PR process.

## License

AGPL-3.0 -- see [LICENSE](LICENSE). Commercial licenses available for organizations that need an alternative to AGPL. See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for details.

Built by [Sagecurator](https://sagewai.ai).
