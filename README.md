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
    <a href="https://sagewai.ai/commercial">Commercial license</a>
  </p>
</div>

---

## What is Sagewai?

**Sagewai is a full-stack, self-hosted platform for building and operating
AI agents in production.** It gives you the agent framework, the admin UI,
the observability, the worker fleet, and the tooling — all in one install.
Point it at any model (Claude, GPT, Gemini, local) and any storage (Postgres,
Redis, your vector store of choice), and start shipping agents that your
team actually uses.

Unlike agent libraries that stop at `agent.run()`, Sagewai gives you:

- A **FastAPI backend** with workflow store, budget/guardrails, analytics
- A **Next.js admin panel** to inspect, debug, and configure runs
- A **worker fleet** with mTLS, anomaly detection, and dispatch normalization
- **MCP server** support so your agents slot into any MCP client
- A **VS Code extension** for directive syntax highlighting and scaffolding
- **Observability** via OpenTelemetry out of the box

All AGPL-3.0. All one `docker compose up`. All self-hostable on your own
hardware, forever.

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
sagewai admin serve --port 8000
```

Hello world in three lines:

```python
from sagewai import Agent

agent = Agent(model="anthropic/claude-sonnet-4.6")
print(agent.run("Explain event loops in one paragraph."))
```

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

## Monorepo map

| Package | What it is | Published as |
|---|---|---|
| [`packages/sdk`](./packages/sdk) | The `sagewai` Python package. Includes `cli`, `mcp`, `admin` (FastAPI), `gateway`, `fleet`, `core`, `engines` as internal subpackages. | `pip install sagewai` |
| [`apps/admin`](./apps/admin) | Next.js admin panel — web UI for agents, workflows, traces. | `ghcr.io/sagewai/admin:<version>` |
| [`apps/backend`](./apps/backend) | Thin Docker image bundling the SDK + a minimal Python runtime, entrypoint = `sagewai admin serve`. | `ghcr.io/sagewai/backend:<version>` |
| [`apps/docs`](./apps/docs) | Next.js docs site deployed to Cloudflare Pages. | [docs.sagewai.ai](https://docs.sagewai.ai) |
| [`apps/vscode-extension`](./apps/vscode-extension) | VS Code extension for directive syntax highlighting and snippets. | VS Code Marketplace |

All five packages ship together on one unified `vX.Y.Z` tag. See
[`.changeset/README.md`](./.changeset/README.md) for the release flow.

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
