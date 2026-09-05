# Work control-plane evolution decisions

This record defines the current product-center boundary for the public
repository. It is intentionally narrower than a package retirement plan: Git
history remains the archive, and a classification does not promise backward
compatibility or authorize mass deletion.

## Classification meanings

- **KEEP** — already supports the Work control plane or remains a useful,
  independently tested capability.
- **ADAPT** — reuse the existing subsystem and add the smallest Work-facing
  surface required; do not rewrite it.
- **FREEZE** — retain the code and tests, but do not extend it as an alternate
  product center without new evidence.
- **RETIRE** — remove an obsolete narrative or surface once direct consumers
  and replacement evidence are checked. This is not a compatibility-alias
  instruction.

## Decisions

| Package or subsystem | Decision | Repository evidence | Boundary |
|---|---|---|---|
| `packages/sdk/sagewai/work` | KEEP | Frozen Work models, event store, Evidence Board, TaskCapsule compiler, operator runtimes, discipline controller, software profile, and tests under `packages/sdk/tests/work/`. | The generic kernel owns Work state and control semantics. Profile-specific software and optional delivery concepts stay behind profile context. |
| `packages/sdk/sagewai/cli/work.py` | ADAPT | `packages/sdk/tests/cli/test_work_cli.py` covers required project scope, direct and GitHub start, status, resume, pending, approval, and explicit Fleet selection. | Keep the CLI a thin operator surface over canonical durable state. Do not create a second lifecycle in command code. |
| `packages/sdk/sagewai/fleet` and `packages/sdk/sagewai/cli/fleet.py` | ADAPT | Existing registration, heartbeat, lease recovery, task persistence, project isolation, and worker auth are reused by Work. Work-specific tests cover heterogeneous capabilities and worker loss. | Reuse Fleet dispatch. Native Codex and Claude authentication stays on workers; central tasks remain credential-free. Standalone agent-task dispatch remains available. |
| `packages/sdk/sagewai/artifacts`, `packages/sdk/sagewai/db`, and `packages/sdk/sagewai/sandbox` | KEEP | The Work store uses the established database layer; content-addressed artifacts and networkless disposable verification have focused tests. | Reuse persistence, artifact, and sandbox primitives. Add no new graph, vector, scheduler, or storage service without recorded failure evidence. |
| `apps/backend` and `packages/sdk/sagewai/admin` | ADAPT | Canonical project-scoped Work routes and pending attention are tested in `packages/sdk/tests/admin/test_work_routes.py`; Fleet authorization has multi-tenant tests. | The backend/store remains lifecycle owner. API routes expose projections and actions, not duplicate state. The first-time setup wizard remains the authentication boundary. |
| `apps/admin` | ADAPT | `apps/admin/app/work/page.tsx`, Work API types, and `apps/admin/e2e/15-work-control.spec.ts`; the coordinator console — `apps/admin/app/board/page.tsx`, `apps/admin/app/tasks/[id]/`, `apps/admin/app/decisions/page.tsx`, `apps/admin/components/coordinator/`, and `apps/admin/e2e/16-coordinator-*.spec.ts` — reuse the existing Next.js application and design system. | Keep a responsive Work Control Console in the existing app. Do not start a native mobile application or add speculative dashboard metrics. |
| `apps/docs`, root `README.md`, and `packages/sdk/README.md` | ADAPT | These are the public entry points and previously centered agent/workflow examples rather than canonical Work commands. | Make Work the primary narrative while linking retained lower-level capabilities accurately. Never publish private design inputs. |
| `packages/sdk/sagewai/engines` and legacy workflow APIs in `packages/sdk/sagewai/core` | FREEZE as product center | Their implementations, examples, and focused test suites remain in the repository and have real consumers independent of Work. | Retain and maintain them as lower-level or parked capabilities. Do not route the canonical Work lifecycle through them or preserve obsolete claims through aliases. |
| `packages/sdk/sagewai/autopilot` | RETIRE as product surface | The coordinator ships what Autopilot narrated, over durable state: `packages/sdk/sagewai/work/tasks/` (schedule, templates, transitions, feed, health), the Task API in `packages/sdk/sagewai/admin/tasks_routes.py`, `sagewai task`, and the console at `apps/admin/app/board`, `apps/admin/app/tasks`, and `apps/admin/app/decisions`. The lifespan runner, the router mount, the sidebar section and pages, the Autopilot e2e specs, the docs page, the README claims, and the `sagewai autopilot` CLI registration are removed. | Remove the product surface, not the code: the package, its tests, `packages/sdk/sagewai/admin/autopilot_*.py`, and examples 28, 35 and 36 stay until a cleanup pass checks direct consumers. This is not a compatibility-alias instruction. |
| `gateway`, `harness`, `mcp`, `connections`, `memory`, `observability`, `safety`, and `sealed` | KEEP as supporting capabilities | Each has an existing package boundary and focused tests; Work can reference capabilities without absorbing their domain models. | Keep available for evidence-backed contracts. They are not mandatory dependencies of a basic software Work run. |
| Software delivery adapters under `packages/sdk/sagewai/work/profiles/software` | KEEP as optional extension | `SoftwareContractContext.delivery` is optional; lifecycle tests prove repository-only completion, while delivery tests exercise explicitly selected contracts. | No deployment provider, Cloudflare account, or external delivery dogfood is required for Core Work. Delivery runs only when a WorkContract activates it. |
| Legacy copy that presents Autopilot, workflows, or the Training Loop as Sagewai's primary path | RETIRE | The actual primary CLI is `sagewai work`; canonical state and pending attention are implemented in WorkStore and shared by CLI/API/console. Git preserves the former copy. | Remove the obsolete product-center claim surgically. Do not delete the underlying packages or manufacture compatibility promises. |

## Revisit rule

Change a decision only with one of these evidence types:

1. a current WorkContract requires the subsystem;
2. a reproducible runtime or retrieval failure identifies the minimum missing
   mechanism;
3. repository consumers and tests prove that a retained surface is obsolete; or
4. an accepted security or correctness finding requires a boundary repair.

Tool availability, credentials, hypothetical future consumers, and convenience
alone are not evidence for expansion.
