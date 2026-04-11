<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/sagewai_logo_dark.svg">
    <img alt="Sagewai" src="brand/sagewai_logo.svg" width="360">
  </picture>

  <h3>Open-source agent infrastructure you own. Build production AI agents with any model.</h3>

  [![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](./LICENSE)
  [![PyPI](https://img.shields.io/pypi/v/sagewai.svg)](https://pypi.org/project/sagewai/)
  [![GHCR](https://img.shields.io/badge/ghcr.io-sagewai%2Fbackend-blue)](https://github.com/sagewai/platform/pkgs/container/backend)
  [![ci-sdk](https://github.com/sagewai/platform/actions/workflows/ci-sdk.yml/badge.svg)](https://github.com/sagewai/platform/actions/workflows/ci-sdk.yml)
  [![ci-admin](https://github.com/sagewai/platform/actions/workflows/ci-admin.yml/badge.svg)](https://github.com/sagewai/platform/actions/workflows/ci-admin.yml)
  [![ci-docs](https://github.com/sagewai/platform/actions/workflows/ci-docs.yml/badge.svg)](https://github.com/sagewai/platform/actions/workflows/ci-docs.yml)

  <p>
    <a href="https://sagewai.ai">Website</a> ·
    <a href="https://docs.sagewai.ai">Docs</a> ·
    <a href="#60-second-quickstart">Quickstart</a> ·
    <a href="#learn-in-5-examples">5-Example Tour</a> ·
    <a href="#video-tutorials">Videos</a> ·
    <a href="https://sagewai.ai/commercial">Commercial license</a>
  </p>
</div>

---

## What is Sagewai?

**Sagewai is a full-stack, self-hosted platform for building and operating
AI agents in production.** It gives you the agent framework, the admin UI,
the observability, the worker fleet, and the tooling — all in one install.
Point it at any model (Claude, GPT, Gemini, local) and any storage (Postgres,
Redis, your vector store of choice), and start shipping agents your team
actually uses.

Unlike agent libraries that stop at `agent.run()`, Sagewai gives you:

- A **FastAPI backend** with workflow store, budget/guardrails, analytics
- A **Next.js admin panel** to inspect, debug, and configure runs
- A **worker fleet** with mTLS, anomaly detection, and dispatch normalization
- **MCP server** support so your agents slot into any MCP client
- A **VS Code extension** for directive syntax highlighting and scaffolding
- **Observability** via OpenTelemetry out of the box

All AGPL-3.0. All one `docker compose up`. All self-hostable on your own
hardware, forever.

---

## 60-second quickstart

```bash
# Full stack (postgres + redis + backend + admin UI)
curl -fsSL https://raw.githubusercontent.com/sagewai/platform/main/docker-compose.yml -o docker-compose.yml
docker compose up -d

# Open the admin UI
open http://localhost:3008
```

Or, for local Python development:

```bash
pip install sagewai
export OPENAI_API_KEY=sk-...   # or ANTHROPIC_API_KEY
sagewai admin serve --port 8000
```

Hello world in three lines:

```python
from sagewai.engines.universal import UniversalAgent
import asyncio

agent = UniversalAgent(name="hello", model="gpt-4o-mini")
print(asyncio.run(agent.chat("Explain event loops in one paragraph.")))
```

---

## Learn in 5 examples

A progressive tour of the SDK. Each example is a complete, runnable Python
file at [`packages/sdk/sagewai/examples/`](./packages/sdk/sagewai/examples/).
Run them in order — they build on each other.

### 1. Hello, agent &nbsp;—&nbsp; [`01_hello_agent.py`](./packages/sdk/sagewai/examples/01_hello_agent.py)

The simplest possible agent. Create it, ask it something, get a response.

```python
from sagewai.engines.universal import UniversalAgent
import asyncio

async def main():
    agent = UniversalAgent(name="hello", model="gpt-4o-mini")
    response = await agent.chat("What are the 5 pillars of Sagewai?")
    print(response)

asyncio.run(main())
```

```bash
export OPENAI_API_KEY=sk-...
python packages/sdk/sagewai/examples/01_hello_agent.py
```

### 2. Give it tools &nbsp;—&nbsp; [`02_tool_agent.py`](./packages/sdk/sagewai/examples/02_tool_agent.py)

Decorate a Python function with `@tool` and the agent can call it. No JSON
schema gymnastics — Sagewai reads your docstring and type hints.

```python
from sagewai.models.tool import tool
from sagewai.engines.universal import UniversalAgent

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"{city}: 18°C, partly cloudy"

agent = UniversalAgent(name="assistant", model="gpt-4o-mini", tools=[get_weather])
# The agent will call get_weather() when the user asks about weather.
```

### 3. Multi-stage workflows &nbsp;—&nbsp; [`05_workflow.py`](./packages/sdk/sagewai/examples/05_workflow.py)

Chain agents. Each stage has its own system prompt, model, and tools. The
output of one feeds into the next.

```python
researcher = UniversalAgent(name="researcher", model="gpt-4o-mini",
    system_prompt="Produce 5 key findings with supporting evidence.")
writer = UniversalAgent(name="writer", model="gpt-4o-mini",
    system_prompt="Turn findings into a polished 300-word summary.")

findings = await researcher.chat(f"Research: {topic}")
summary  = await writer.chat(f"Write about these findings: {findings}")
```

### 4. Guardrails &nbsp;—&nbsp; [`06_guardrails.py`](./packages/sdk/sagewai/examples/06_guardrails.py)

Add PII redaction, content filters, and budget caps to an agent. They run
before every LLM call — no plumbing, just pass them at construction.

```python
from sagewai.safety.guardrails import ContentFilter
from sagewai.safety.pii import PIIGuard

agent = UniversalAgent(
    name="safe-agent",
    model="gpt-4o-mini",
    guardrails=[
        PIIGuard(action="redact"),
        ContentFilter(blocked_terms=["confidential"], action="block"),
    ],
)
```

### 5. Production fleet &nbsp;—&nbsp; [`20_fleet_workers.py`](./packages/sdk/sagewai/examples/20_fleet_workers.py)

When you're ready to scale, register remote workers to a dispatcher. Jobs
fan out across a pool; each worker runs in its own process or node with
mTLS, capability labels, and approval gates.

```python
from sagewai.fleet import FleetDispatcher, InMemoryFleetRegistry, InMemoryTaskStore

registry = InMemoryFleetRegistry()
dispatcher = FleetDispatcher(store=InMemoryTaskStore(), poll_timeout=2.0)
# Workers register and pull jobs. The dispatcher handles routing,
# retries, and worker health.
```

---

## More examples — feature matrix

Every major subsystem has a dedicated runnable example. Drop into any of
them when you're ready to go deeper.

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
| 24 | [`24_harness_agent.py`](./packages/sdk/sagewai/examples/24_harness_agent.py) | Agents running under harness with full audit trail |

---

## Video tutorials

Video topics are scripted and staged in
[`apps/docs/app/docs/guides/video-tutorials/`](./apps/docs/app/docs/guides/video-tutorials/page.mdx).
Recordings are rolling out on YouTube and the docs site; URLs will be
wired in as each episode ships.

### Start here (first 3 videos)

| # | Title | Length | What you'll learn |
|---|---|---|---|
| 1 | **Sagewai in 5 minutes: your first AI agent** | 5 min | Install sagewai, create a 4-line agent, add a custom tool, run it. Zero to working agent. |
| 2 | **Run AI agents for free with Ollama + Sagewai** | 8 min | Install Ollama, pull `llama3.1`, create an agent with `providers.ollama()`. No API keys, no cloud costs, 100% local. |
| 3 | **Sagewai vs LangChain vs CrewAI: an honest comparison** | 12 min | Build the same research agent in all three frameworks. See where Sagewai is different — cost control, fleet, local inference. |

### Core features (next 5)

| # | Title | Length |
|---|---|---|
| 4 | Building a research agent with memory | 10 min |
| 5 | RAG in 10 minutes: PDF Q&A with the context engine | 10 min |
| 6 | Multi-agent workflows: researcher, analyst, writer | 12 min |
| 7 | Sagewai directives: the prompt preprocessor | 8 min |
| 8 | Safety & guardrails: PII, hallucination, budget | 10 min |

**Full 20-episode playlist:** [`apps/docs/app/docs/guides/video-tutorials/`](./apps/docs/app/docs/guides/video-tutorials/page.mdx) — covers VS Code extension, MCP server, K8s deployment, fine-tuning with Unsloth, durable workflows, self-learning agents, full-stack apps, and more.

---

## Local development

```bash
git clone git@github.com:sagewai/platform.git
cd platform
./scripts/bootstrap.sh          # installs uv + pnpm + syncs everything
```

### Test and benchmark targets

Every check has its own `make` target with a clear name. Run `make help`
to see them all inline; the ones you'll reach for every day:

| Target | What it runs | Expected duration |
|---|---|---|
| `make smoke` | 29 fast smoke tests (no LLM, no services). Pre-commit sanity check. | ~0.1 s |
| `make test` | Full SDK unit test suite — 2904 tests, all mocks. | ~10 s |
| `make perf` | Performance micro-benchmarks with fixed time budgets. Catches 10x regressions in hot paths. | ~0.1 s |
| `make build` | Build sdk wheel + admin + docs + vscode-extension. | ~2 min |
| `make dev-all` | Run backend (FastAPI) + admin UI concurrently on localhost. | — (long-running) |
| `make compose-up` | Full stack via root `docker-compose.yml` (postgres + redis + backend + admin). | — (long-running) |

Package-scoped variants exist for targeted iteration: `sdk-test`, `sdk-smoke`, `sdk-perf`, `sdk-build`, `sdk-lint`, `admin-dev`, `admin-build`, `docs-dev`, `docs-build`, `vscode-build`, `backend-build`.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full flow.

---

## Why Sagewai vs. the alternatives?

| | Sagewai | LangChain / LangGraph | CrewAI | OpenAI Agents SDK |
|---|---|---|---|---|
| Self-hostable backend + admin UI | ✅ | ❌ (library only) | ❌ | ❌ |
| Production worker fleet with mTLS | ✅ | ❌ | ❌ | ❌ |
| Budget, guardrails, analytics built-in | ✅ | Partial (LangSmith = SaaS) | ❌ | ❌ |
| Any-model, any-provider | ✅ | ✅ | ✅ | OpenAI-first |
| One-command full stack (compose) | ✅ | ❌ | ❌ | ❌ |
| License for embedding in proprietary product | Commercial | MIT | MIT | Apache |

Sagewai is the answer when "I want LangSmith but self-hosted, with the
admin UI, the worker fleet, and no vendor lock-in."

---

## Monorepo map

| Package | What it is | Published as |
|---|---|---|
| [`packages/sdk`](./packages/sdk) | The `sagewai` Python package. Includes `cli`, `mcp`, `admin` (FastAPI), `gateway`, `fleet`, `core`, `engines` as internal subpackages. | `pip install sagewai` |
| [`apps/admin`](./apps/admin) | Next.js admin panel — web UI for agents, workflows, traces. | `ghcr.io/sagewai/admin:<version>` |
| [`apps/backend`](./apps/backend) | Thin Docker image bundling the SDK + a minimal Python runtime, entrypoint = `sagewai admin serve`. | `ghcr.io/sagewai/backend:<version>` |
| [`apps/docs`](./apps/docs) | Next.js docs site deployed to Cloudflare. | [docs.sagewai.ai](https://docs.sagewai.ai) |
| [`apps/vscode-extension`](./apps/vscode-extension) | VS Code extension for directive syntax highlighting and snippets. | VS Code Marketplace |

All five packages ship together on one unified `vX.Y.Z` tag. See
[`.changeset/README.md`](./.changeset/README.md) for the release flow.

### Client wrappers (separate repos)

Thin language-idiomatic clients that talk to a running Sagewai backend over
HTTP — for teams where the stack isn't Python. They're standalone so each
language has its own release cadence and packaging ecosystem.

| Language | Repo |
|---|---|
| Python (standalone client) | [sagewai/sagewai-python](https://github.com/sagewai/sagewai-python) |
| TypeScript / JavaScript | [sagewai/sagewai-ts](https://github.com/sagewai/sagewai-ts) |
| Go | [sagewai/sagewai-go](https://github.com/sagewai/sagewai-go) |
| Rust | [sagewai/sagewai-rs](https://github.com/sagewai/sagewai-rs) |
| Java | [sagewai/sagewai-java](https://github.com/sagewai/sagewai-java) |
| Kotlin | [sagewai/sagewai-kotlin](https://github.com/sagewai/sagewai-kotlin) |
| C# / .NET | [sagewai/sagewai-dotnet](https://github.com/sagewai/sagewai-dotnet) |
| Scala | [sagewai/sagewai-scala](https://github.com/sagewai/sagewai-scala) |
| Ruby | [sagewai/sagewai-ruby](https://github.com/sagewai/sagewai-ruby) |
| PHP | [sagewai/sagewai-php](https://github.com/sagewai/sagewai-php) |
| Swift | [sagewai/sagewai-swift](https://github.com/sagewai/sagewai-swift) |
| C / C++ | [sagewai/sagewai-cpp](https://github.com/sagewai/sagewai-cpp) |
| Dart | [sagewai/sagewai-dart](https://github.com/sagewai/sagewai-dart) |
| Flutter | [sagewai/sagewai-flutter](https://github.com/sagewai/sagewai-flutter) |
| React Native | [sagewai/sagewai-react-native](https://github.com/sagewai/sagewai-react-native) |
| Elixir | [sagewai/sagewai-elixir](https://github.com/sagewai/sagewai-elixir) |
| Perl | [sagewai/sagewai-perl](https://github.com/sagewai/sagewai-perl) |

The Python wrapper is for apps that don't want to pull in the full SDK
dependency tree — the monorepo `packages/sdk` remains the primary Python
surface for agent authoring.

---

## Contributing

Pull requests are welcome on bug fixes, docs, and small features. For
larger changes, open a GitHub Discussion first so we can align on scope
before you write code.

By contributing, you agree to the [Contributor License Agreement](./CLA.md).
This is a **Copyright License Grant** — you keep copyright of your
contribution while granting Ali Arda Diri a broad license to use and
relicense it under AGPL-3.0 and commercial terms. This is the same model
used by Ghost, Cal.com, and MariaDB to maintain a sustainable dual-license
OSS business.

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for coding conventions and the
PR flow, and [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md) for community
expectations.

## Roadmap

Roadmap lives on the public [Sagewai Roadmap board](https://github.com/orgs/sagewai/projects)
(read-only for visitors, maintainer-write for the core team). Anything
not on the board is not planned.

## Governance

Sagewai uses a closed-governance model: branch protection, CODEOWNERS
review requirements, and a maintainer-gated issue tracker. Community
input flows through GitHub Discussions; Issues are used as an internal
worklist by the maintainer team. This is the same model used by Bun,
Deno, and Tauri for commercial-OSS projects.

## License

Sagewai is licensed under the **GNU Affero General Public License v3.0 or
later** ([LICENSE](./LICENSE)).

A **commercial license** is available for organizations that need to:
- Embed Sagewai in proprietary software without AGPL-3.0 obligations
- Offer Sagewai as a managed service (SaaS) without sharing platform code
- White-label or remove Sagewai branding

See [`COMMERCIAL-LICENSE.md`](./COMMERCIAL-LICENSE.md) and contact
[licensing@sagewai.ai](mailto:licensing@sagewai.ai) for terms.

For the rationale behind the dual-license model and the distinction
between *using* Sagewai vs. *modifying* it, see
[`LICENSE_FAQ.md`](./LICENSE_FAQ.md).

---

<div align="center">
  <sub>Built in Berlin by <a href="https://github.com/sagecurator">Ali Arda Diri</a>. Sagewai is a trademark of Ali Arda Diri.</sub>
</div>
