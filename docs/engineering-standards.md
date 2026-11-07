# Engineering standards: UI, observability, data, and content

These standards apply to substantial projects. Keep project-specific choices and
commands in that project's own instructions. Adopt a component or service only
when the product needs it; use the smallest working deployment.

## UI and frontend

- Build substantial React UIs with shadcn/ui components and blocks, Tailwind CSS,
  and AI Elements patterns for chat, agents, tool calls, reasoning, plans, prompts,
  and related surfaces. Copy and maintain the components in the project; AI
  Elements is a pattern library, not a runtime dependency.
- Make theme, radius, font, spacing, and density semantic tokens backed by CSS
  variables. Components consume tokens, never hard-coded colors or pixel values.
  Support light/dark themes and user-visible density or brand changes without
  editing each component.
- Drive navigation, labels, icons, permissions, and layout variants from one typed
  configuration. Use the shadcn preset's icon set (Lucide by default); every menu
  and navigation item has an icon and accessible label.
- Compose with shadcn primitives rather than adding another component library or
  hand-written component CSS. Keep focus, keyboard, contrast, loading, error,
  empty, and responsive states in each user flow.
- For migrations, first define tokens and the shared shell, then move one route or
  surface at a time. Check keyboard navigation and representative screenshots at
  phone and desktop widths, then remove replaced styles and dependencies.
- For substantial UI, use React or Next.js so the exact shadcn/ui and AI
  Elements standard applies. Existing Vue/Nuxt apps must integrate or migrate
  substantial UI to that stack; shadcn-vue is a separate port and does not meet
  this machine-wide requirement. Small Vue/Nuxt projects still follow the token
  and accessibility rules.

## Observability and monitoring

- Instrument request, job, queue, and external-call boundaries with OpenTelemetry
  trace context and useful attributes. Expose Prometheus-compatible metrics or
  send OTLP through an OpenTelemetry Collector. Keep one collection path per signal.
- Use VictoriaMetrics for metrics, VictoriaLogs for structured logs, and Grafana
  for dashboards. A Prometheus server may scrape and remote-write to
  VictoriaMetrics where already deployed; vmagent or an OTel Collector can collect
  instead. Add a trace backend only when trace retention and querying are needed.
- Define service, environment, operation, outcome, and tenant-safe identifiers
  consistently. Bound label cardinality: never use user IDs, request IDs, raw
  paths, prompts, or unbounded tenant names as metric labels. Correlate logs and
  traces with trace IDs; redact secrets and personal data before export.
- Monitor user-visible failure, latency, saturation, and backlog. Each alert has
  an owner, threshold or SLO, severity, runbook, and a tested failure path. Prefer
  actionable alerts over dashboard-only metrics; keep rules and dashboards in
  version control. Set retention and sampling from actual query and cost needs.

## Analytics and data lake

- Store immutable raw events as schema-versioned JSONL in S3 or Cloudflare R2,
  partitioned by date and safe tenant boundary. Include event ID, timestamp,
  source, schema version, and consent or retention classification. Keep secrets and
  unnecessary personal data out of the lake.
- Compact validated raw events into Parquet for routine analytics. Partition on
  fields actually used to filter; avoid tiny files; record schema evolution and
  lineage. Query Parquet with DuckDB locally or in bounded jobs, including over
  S3-compatible storage. Do not put DuckDB inside a latency-sensitive service.
- Use Lance only for datasets that need fast random access, embeddings, vector or
  full-text indexes, or multimodal training. Parquet remains the interoperable
  default for ordinary tabular analytics.
- Use AWS SNS + SQS for fanout and durable workers on AWS, or Cloudflare Queues
  with R2 event notifications on Cloudflare. Treat delivery as at least once:
  idempotent consumers, stable event IDs, bounded retries, dead-letter handling,
  and replay from the raw object store. Verify current regional cost and limits
  before choosing a provider; object storage itself is not a work queue.

## Content creation with Payload

- Use Payload CMS for editor-managed pages, articles, media, and reusable content
  blocks in a Next.js app. Model repeatable content as collections and site-wide
  content as globals; generate types from the schema.
- Put editorial roles, tenant or project scope, publication state, and field-level
  restrictions in Payload access control. Verify local API calls enforce access
  when made on behalf of a user. Use drafts, versions, and preview where editors
  need them, and keep media in object storage with explicit access rules.
- Keep runtime configuration, operational events, and application state in their
  owning systems. Treat CMS content as untrusted input at rendering boundaries.
  Check supported Next.js, Node, and adapter versions before installation.

## Backend services

A new deployable backend service exposes HTTP, MCP, and CLI adapters over the
same application use cases. A library is not a service. Keep domain rules in
one place; adapters translate requests, identities, errors, and results.
Existing services change their recorded API contract first and add missing
adapters through explicit migration, without creating a competing contract.

- **DDD by default.** Name bounded contexts and use the domain's language in code
  and contracts. Put invariants in domain types or aggregates, orchestration in
  application services, and storage/external calls behind ports. Keep transport
  and database models out of the domain; add tactical patterns only when they
  protect a real invariant.
- **API first.** For HTTP APIs, define and review a versioned OpenAPI contract
  before building handlers. Specify authentication, tenant scope, schemas,
  errors, pagination, and idempotency. Pin a tooling-supported OpenAPI 3.x
  version; lint and check breaking changes in CI, then generate clients/types
  from the contract. Use RFC 9457 Problem Details where HTTP errors need a
  machine-readable body.
- **MCP enabled.** Publish an intentional, typed MCP tool/resource surface for
  agent-useful capabilities. Map it to the same application services and
  authorization as HTTP; never expose raw database operations or every HTTP
  endpoint automatically. Keep local use on stdio and expose remote HTTP only
  with authentication, tenant isolation, bounded input, timeouts, and audit.
  Pin an SDK and protocol version supported by the clients in use.
- **CLI enabled.** Provide versioned commands for serving, config validation,
  migrations, health, and relevant domain operations. CLI paths use the same
  application services and permissions; offer machine-readable output, stable
  exit codes, `--help`, and noninteractive operation for automation.
- **Twelve-factor runtime.** Keep build, release, and run separate; processes
  start fast, shut down gracefully, and hold no durable state. Treat databases,
  queues, and object stores as attached resources. In server mode, emit structured
  logs to a platform-collected stream; when stdout carries MCP stdio or CLI
  results, send diagnostics to stderr. Keep dev/prod behavior close.
  Inject deploy-specific config and secrets from the environment or a secret
  manager; typed loaders may combine files, env, and flags without committing
  secrets. Validate at startup; publish immutable snapshots for runtime changes.
- **Safe boundaries.** Authenticate and authorize every adapter, resolve tenant
  scope before application work, use least privilege, deadlines, bounded work,
  and idempotency for commands that can be retried. Record trace IDs and
  redact sensitive data. Test domain claims once, then a small set of HTTP,
  MCP, and CLI contract paths; avoid repeating the same journey three times.

## Language and framework defaults

| Stack | Small required change gate | Additional checks when relevant |
| --- | --- | --- |
| Go | `gofmt`, `go vet`, build, module tidiness, focused `go test` | Targeted `-race` and integration; `govulncheck` on dependency changes or scheduled gate |
| Rust | `cargo fmt --check`, `cargo clippy -- -D warnings`, focused `cargo test` | Full tests and integration; `tracing` with OTel at service boundaries; use `serde` for wire data |
| Python | `uv` lock/sync, Ruff, type check changed code, focused `pytest` | Full suite and integration; Pydantic for external/config schemas; use `pytest.mark.parametrize` for variants |
| Node/TypeScript | Pinned `pnpm` install, formatter/linter, `tsc --noEmit`, focused unit tests | Browser journeys only for user-visible contracts; use strict TypeScript and schema validation at external boundaries |
| React/Next.js | Typecheck and build changed app; component and route behavior tests | Playwright for critical flows; keep server/client boundaries explicit and apply the UI standard above |
| Vue/Nuxt | Typecheck, build, focused component/route tests | Integrate or migrate substantial UI to the required React stack; keep tokens and accessibility while migrating |

### Go service rules

- Keep packages cohesive and dependencies explicit; prefer the standard library.
  Pass `context.Context` first, wrap errors with `%w`, avoid mutable globals and
  `init` side effects, and define narrow interfaces at the consuming boundary.
- Use typed config with explicit precedence: defaults, files, environment, then
  supplied flags. Koanf is useful for multiple sources; keep schema and validation
  independent of the loader. Inject validated config through constructors. For
  runtime and multitenant changes, publish immutable versioned snapshots and
  resolve tenant overrides at the boundary.
- Give AI tasks a goal, acceptance claim, file scope, and proof command. Keep the
  diff small and review it against the claim; use independent review when the
  repository workflow requires one.
- Test observable behavior with named table rows and `t.Run`. Use `t.TempDir`,
  `httptest`, controlled clocks, and explicit synchronization. No sleeps, live
  services, shared mutable fixtures, retries, or tests of getters and mock calls
  just to raise coverage. Fix flaky tests before merging.
- Keep the Go gate short: format, vet, build, module and generated-file drift,
  affected-package tests and dependents. Run the full unit suite if impact
  selection is unreliable; target race and integration checks to changed
  boundaries, with broader checks on scheduled or release gates.

Use table-driven or parameterized tests for behavior variants, deterministic
fixtures and clocks, and no retries to mask flaky tests. Run broader suites,
race or concurrency checks, dependency audits, and security scans on the
appropriate change, scheduled, or release gate. Do not add a coverage target
that rewards tests of implementation details.

## Primary references

- [Domain-driven design and bounded contexts](https://martinfowler.com/bliki/BoundedContext.html)
- [OpenAPI specification](https://spec.openapis.org/oas/) and [HTTP Problem Details](https://www.rfc-editor.org/rfc/rfc9457.html)
- [Model Context Protocol](https://modelcontextprotocol.io/specification) and [twelve-factor app](https://12factor.net/)
- [shadcn/ui AI Elements](https://ui.shadcn.com/docs/ai-elements)
- [VictoriaMetrics and OpenTelemetry](https://docs.victoriametrics.com/opentelemetry/)
- [DuckDB Parquet](https://duckdb.org/docs/data/parquet) and [S3/R2 access](https://duckdb.org/docs/current/guides/network_cloud_storage/s3_import)
- [Lance](https://lance.org/)
- [Cloudflare R2 event notifications](https://developers.cloudflare.com/r2/buckets/event-notifications/) and [Queues delivery](https://developers.cloudflare.com/queues/reference/how-queues-works/)
- [Payload concepts](https://payloadcms.com/docs/getting-started/concepts) and [access control](https://payloadcms.com/docs/access-control/overview)
