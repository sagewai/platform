# Sage Admin

Full-featured admin console for managing agents, workflows, fleet, harness, intelligence, context, memory, and platform settings.

## Tech Stack

- Next.js 16, React 19, TypeScript
- Tailwind CSS 4
- recharts, @xyflow/react, d3-sankey, dagre, framer-motion
- react-markdown, react-syntax-highlighter, qrcode.react, driver.js, js-yaml

## Development

```bash
# From the monorepo root
just admin-dev

# Or directly
cd apps/admin && pnpm dev

# With backend API URL (required for full functionality)
NEXT_PUBLIC_ADMIN_API_URL=http://localhost:8000/admin pnpm dev
```

## Pages

- `/` — Dashboard overview
- `/agents` — Agent registry, detail (`/[name]`), runs, templates
- `/workflows` — Builder, history, registry, templates, dispatch, approvals, DLQ, workers
- `/fleet` — Worker fleet management, enrollment keys, audit
- `/harness` — API harness, virtual keys, policies, analytics
- `/intelligence` — Intelligence dashboard, LLM spend tracking
- `/context` — Context Engine dashboard, documents, search, lifecycle, directives
- `/memory` — Knowledge graph explorer, vector store browser
- `/playground` — Interactive agent playground
- `/analytics` — Cost, model, network, and performance analytics
- `/settings` — Organization, projects, models, tokens, triggers, billing, health, notifications
- `/workspace` — Members, teams, providers
- `/safety` — Guardrails, audit log
- `/eval` — Datasets, run evaluations, reports
- `/tools` — MCP servers, model router, Ollama
- `/operations` — Budget management
- `/sessions` — Session inspector
- `/runs` — Run history with detail view
- `/tv` — TV dashboard mode

## Port

Default: 3008
