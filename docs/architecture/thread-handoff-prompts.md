# Thread handoff prompts

**Status:** session-handoff aid (delete or move to `docs/superpowers/notes/` once all listed threads are spawned)
**Created:** 2026-04-25
**Source:** end of architecture-foundation PR (#153) thread

This document holds ready-to-paste opening prompts for the next 8 work threads. Each prompt is self-contained: it tells a fresh AI session exactly what to do, what docs to read first, what's already shipped, and what success looks like.

**Open new threads in this order.** Threads 1-3 are sequential blockers; Threads 4-8 can run in parallel after Thread 1 lands.

---

## Thread 1 — Mode-aware runner + migration + just hygiene  ✅ SHIPPED (PR #155, 2026-04-26)

**Status:** Run-level execution mode is shipped. Migration 005, `ExecutionMode` enum, and `WorkflowRun.execution_mode` are live on `main`. Migration headers and justfile grouping are also done. **Per-step mode override is a follow-up** — write a separate plan for it before opening a new thread.

**Goal:** Make the architecture docs' invariants real in code. Small, focused refactor.

**Prompt:**

```
You are working on `sagewai/platform`. Read first, in order:
- docs/architecture/runtime-topology.md
- docs/architecture/execution-modes.md
- docs/architecture/execution-backends.md

Three pieces of work, each its own commit:

A. MODE-AWARE RUNNER — first-class execution mode

   Currently `WorkflowRun` carries `requires_sandbox_mode` (a SandboxMode
   enum: NONE/PER_RUN/PER_TOOL/PER_WORKER) which is a Plan 3a artifact
   that doesn't capture the architecture's Mode 0/1/2/3/3b taxonomy.

   Add `WorkflowRun.execution_mode: ExecutionMode` field where
   ExecutionMode ∈ {BARE, SANDBOXED, IDENTITY, FULL, FULL_JIT} (a new
   enum in `core/state.py`). Worker dispatch reads this field and
   chooses the run path. Keep `requires_sandbox_mode` for backward
   compat but compute it from execution_mode at enqueue:
     BARE → SandboxMode.NONE
     SANDBOXED, IDENTITY, FULL, FULL_JIT → SandboxMode.PER_RUN
   Migration 005 adds the column.

   Per-step mode override is a follow-up — for now the run-level mode
   is what the worker sees. Document this limitation in the migration
   commit message.

B. MIGRATION HEADERS — annotate, don't restructure

   For each migration in packages/sdk/sagewai/db/migrations/versions/,
   add (or update) the docstring header to include:
   - The spec / plan that drove it (e.g., "Sealed-i Task 7")
   - The PR number it shipped in
   - One-sentence summary

   Don't squash. Don't reorder. Don't rename. Just annotate.

C. JUSTFILE GROUPING

   `justfile` recipes have accumulated. Group by lifecycle in this
   order: bootstrap / dev / test / smoke / perf / build / deploy.
   Add a one-line comment to each recipe. Don't delete any recipe
   without checking with the operator.

Verification:
- `just smoke` passes
- `just test` passes (full SDK suite)
- `alembic upgrade head` and `alembic downgrade base && upgrade head`
  both succeed
- New `execution_mode` column round-trips through save_run / load_run

Branch: `refactor/mode-aware-runner`
PR title: `refactor: mode-aware runner + migration headers + just grouping`
```

**Pre-reads:** the 3 architecture docs above. **Don't read** any plan or spec before starting — the architecture docs are sufficient.

**Estimated size:** 1 day. Three commits, single PR.

---

## Thread 1.5 — Pre-v1.0 codebase cleanup audit

**Goal:** Sagewai has been built rapidly over many months across many AI threads. There is accumulated obsolete, unnecessary, and hallucinated code. Before v1.0 release, do a comprehensive audit and remove the cruft. This is a multi-PR effort, not a single session.

**Why this is its own thread:** Thread 1 is a focused refactor. This is an audit + many small deletions. Conflating them would mask both. Run Thread 1.5 in parallel sessions, one PR per category below.

**Prompt:**

```
You are working on `sagewai/platform`. Pre-v1.0 cleanup audit.

Read first, in order:
- docs/architecture/runtime-topology.md
- docs/architecture/security-tiers.md
- docs/architecture/execution-modes.md
- docs/architecture/execution-backends.md
- README.md (top-level)
- packages/sdk/README.md
- CLAUDE.md (project context — what's currently shipped)

This thread is an AUDIT, not a redesign. Goal: shrink lines-of-code,
remove dead surfaces, kill stale docs, prepare a clean v1.0 baseline.
The architecture docs are the source of truth; anything in code that
contradicts them is a candidate for removal.

YOU WILL DO THIS IN PHASES, EACH ITS OWN PR. Open one tracking
issue first to enumerate findings, then a PR per category.

PHASE 0 — Inventory (read-only, produces a tracking issue)

Walk every package and apps/ subtree. Build a list:

- DEAD CODE: imports / functions / classes / modules with zero
  external references (use `vulture` or manual grep). Examples to
  look for:
    * older agent registry code superseded by `sagewai.fleet`
    * older sandbox abstractions superseded by `sagewai.sandbox`
    * unused MCP server scaffolding
    * "experimental" admin pages (e.g., apps/admin/app/hud-ironman/
      lives untracked — decide: ship as a real feature, or delete)
    * stale CLI commands that no longer match shipped admin surfaces
    * older training-data export paths superseded by Sealed-aware ones

- HALLUCINATED CODE: code that was generated but does nothing useful
  or doesn't actually integrate. Signs:
    * functions with TODO / FIXME / pass-only bodies
    * orphan modules with no callers and no tests
    * classes with abstract methods but no concrete subclass
    * tests that test things that don't exist anymore (skipped
      indefinitely, or asserting against ghost APIs)

- OBSOLETE PATTERNS: code from earlier architectures that's been
  superseded but not removed. Specifically:
    * Pre-Sealed env-handling that bypassed the SecretProvider
    * Pre-Plan-3a fleet code that hardcoded routing
    * Pre-Sealed-iii.A revocation-less flows where revocation should
      now apply
    * Tool execution paths that don't go through the tool runner

- DUPLICATED LOGIC: same operation implemented in multiple places.
  Examples:
    * Multiple "load admin state" implementations
    * Per-package logging helpers that should use the canonical one
    * Per-package retry / timeout helpers
    * Multiple JSONB serializers for the same shape

- STALE DOCS: markdown files in docs/, READMEs in subpackages, and
  inline comments referring to the old architecture. The 4 new
  architecture docs are the contract; anything contradicting them
  is wrong.

- ABANDONED EXPERIMENTS: untracked directories, untracked files,
  half-finished features. Decide per item: ship it, document it as
  a follow-up, or delete it. The current `git status` shows:
    .superpowers/   (likely a local cache; should be in .gitignore)
    apps/admin/app/hud-ironman/  (decide: feature or delete)
  Run `git status --ignored | grep -v node_modules | grep -v __pycache__`
  for the full picture.

- STALE TESTS: tests that have been skipped for >2 months, tests that
  mock things no longer in the codebase, tests that always pass
  trivially.

- STALE CI: github actions workflow steps that build things no longer
  shipped, dependencies installed that no longer have callers.

Output of phase 0: a single tracking issue titled
`[meta] Pre-v1.0 cleanup audit — findings and PR plan`
that lists each finding by category, with file:line references and
recommended action (delete / refactor / ship / decide).

PHASE 1+ — Execute, one PR per category

After the tracking issue is approved, open one PR per category.
Don't bundle. Each PR title is `cleanup: <category> — <one-line
summary>`. Each PR description references the tracking issue.

Categories (rough ordering):
1. Stale tests — easiest to verify (delete + tests still pass)
2. Dead code — `vulture`-detectable + spot-check
3. Hallucinated code — judgment call per item
4. Obsolete patterns — careful: ensure the replacement is actually
   in use before deleting the old
5. Duplicated logic — consolidate to one canonical implementation
6. Stale docs — replace with cross-references to architecture docs
7. Abandoned experiments — per-item decision
8. Stale CI — verify the build still passes after each step removed

Each PR is reviewable on its own. Each PR's verification:
- `just test` passes
- `just smoke` passes
- `cd apps/admin && pnpm exec tsc --noEmit` passes
- Existing CI (lint, audit, build) green

DO NOT:
- Don't restructure files that are not stale (refactoring for taste
  alone is out of scope)
- Don't introduce new abstractions ("while I'm here, let me also...")
- Don't break public APIs (deprecate first, document, then remove
  in v1.1+)
- Don't remove tests for shipped features (only stale tests testing
  ghost behavior)
- Don't squash migrations (covered in Thread 1)

When in doubt: leave it. The author can always add more in another PR.
```

**Pre-reads:** the 4 architecture docs + READMEs + CLAUDE.md.

**Estimated size:** Phase 0 alone is ~1 day of audit work. Phases 1-8 are ~1 day each = ~1 week of small PRs. Total: 8-10 PRs over ~2 weeks of part-time work.

**This thread blocks v1.0.** Until it lands, the codebase has unknown surface area and the release notes can't be honest.

---

## Thread 2 — Plan 1.5 sandbox pooling (Docker first; backend-agnostic design)

**Goal:** pool sandboxes between runs so cold-start overhead is amortized; design over the SandboxBackend Protocol so KubernetesBackend and LambdaBackend (later threads) can plug in without changing the pooling logic.

**Prompt:**

```
You are working on `sagewai/platform`, branch `feat/plan-1.5-pooling`
off latest main.

Read first, in order:
- docs/architecture/runtime-topology.md
- docs/architecture/execution-modes.md (focus on "Mode 1+" sections)
- docs/architecture/execution-backends.md (focus on "Pool support" rows
  in the Mode×Backend matrix and per-backend "Pool reuse" translations)
- docs/superpowers/specs/2026-04-25-sealed-iii-a-revocation-design.md
  (the cleanup_run hook contract you'll consume)

This is a brainstorm + spec + plan + implement task. Use the
brainstorming skill to design first; the writing-plans skill to plan;
the subagent-driven-development skill to implement.

Scope:
- Pool warm sandboxes between runs (Mode 1+ steps)
- Use Sealed-iii.A's `cleanup_run` hook to scrub Tier-2 env on release
- Pool sizing: per-(image, mode, network_policy, image_variant) tuple
- Pool eviction: LRU + idle-timeout
- Pool stats: emit `pool.acquire`, `pool.release`, `pool.evict`,
  `pool.warm` audit / OTel events
- Admin UI: read-only stats panel showing per-pool warm count, hit
  rate over the last hour, last evict time
- Initial implementation: DockerBackend only, but the SandboxPool
  class must be backend-agnostic — KubernetesBackend (Thread 3)
  must plug in without changes to SandboxPool

Out of scope:
- KubernetesBackend pool implementation (Thread 3)
- LambdaBackend pool implementation (provisioned concurrency — own thread)
- Per-step mode (Thread 1's mode-aware runner makes per-run sufficient
  for now)

Verification:
- Cold-start latency baseline (with pool) ≤ 200ms p95 on Docker
- Existing Sealed tests still pass (cleanup_run integrates correctly)
- Pool stats visible in admin UI
- `just bench-pool` micro-benchmark added

Spec to: docs/superpowers/specs/YYYY-MM-DD-plan-1-5-sandbox-pooling-design.md
Plan to: docs/superpowers/plans/YYYY-MM-DD-plan-1-5-sandbox-pooling.md
```

**Pre-reads:** 3 architecture docs + Sealed-iii.A spec.

**Estimated size:** 2-3 days (~15-20 task plan).

---

## Thread 3 — Plan SBX-K8S Kubernetes sandbox backend

**Goal:** add `KubernetesBackend` so Sagewai can run sandboxes as k8s pods. Production-scale unlock.

**Prompt:**

```
You are working on `sagewai/platform`, branch `feat/sandbox-k8s-backend`
off latest main (after Plan 1.5 pooling lands — Thread 2).

Read first, in order:
- docs/architecture/runtime-topology.md
- docs/architecture/execution-backends.md (focus on the Per-backend
  primitive translations table)
- docs/architecture/execution-modes.md (Mode 0 / 1 / 2 / 3 / 3b — all
  must work on k8s)
- packages/sdk/sagewai/sandbox/docker_backend.py (the reference
  implementation; the new backend mirrors its shape)
- packages/sdk/sagewai/sandbox/backend.py (the SandboxBackend Protocol)

This is a brainstorm + spec + plan + implement task.

Scope:
- New `KubernetesBackend` implementing SandboxBackend
- Pod-per-sandbox model (use Job for short-lived, Pod for pooled)
- Env injection via pod.spec.containers[0].env
- Network policy translation:
    NONE         → NetworkPolicy resource: egress: [], ingress: []
    EGRESS_ONLY  → egress to allowed CIDRs
    FULL         → no NetworkPolicy (default cluster policy applies)
- Resource limits → resources.limits.memory/cpu
- Workdir mount → emptyDir volume
- Tool runner exec → kubectl exec (via official kubernetes-python client,
  not shell-out)
- Pool integration → warm pods via Deployment with min-replicas;
  pool returns pre-warmed pods + recycles after run completes
- Image variants → standard k8s image pull; same `ghcr.io/sagewai/...`
  catalog as Docker
- Cluster credentials → `sagewai admin sandbox config k8s` command
  to set kubeconfig path / in-cluster service account / namespace
- Worker capability label: `sandbox.backend=kubernetes` so fleet
  routes appropriately

Out of scope:
- StatefulSet support (future, for sticky-pod workflows)
- Per-tenant namespace isolation (initial: single namespace; tenant
  isolation is a follow-up if multi-tenancy on shared k8s is needed)
- LambdaBackend (separate, optional thread)

Verification:
- `KubernetesBackend` passes the same SandboxBackend conformance
  tests as `DockerBackend`
- Mode 0/1/2/3/3b all execute on a real k8s cluster (kind / minikube
  for dev; integration test gated on `SAGEWAI_K8S_TEST_KUBECONFIG`)
- Plan 1.5 pool integration works: warm-pod-pool serves Mode 3
  workflows
- Sealed-iii.A `cleanup_run` works via kubectl exec to unset env

Spec to: docs/superpowers/specs/YYYY-MM-DD-sandbox-k8s-backend-design.md
Plan to: docs/superpowers/plans/YYYY-MM-DD-sandbox-k8s-backend.md
```

**Pre-reads:** 3 architecture docs + DockerBackend code + SandboxBackend Protocol.

**Estimated size:** 3-4 days (~20-25 task plan). Major plan. Likely needs decomposition into sub-PRs (backend impl → pool integration → admin UI → docs).

---

## Thread 4 — Plan ART (artifact destination resolver)

**Goal:** formalise where Mode 3 CLI agent outputs land (GitHub repo / S3 / mounted folder); inject the right credentials per workflow.

**Prompt:**

```
You are working on `sagewai/platform`, branch `feat/plan-art-artifact-dest`
off latest main.

Read first, in order:
- docs/architecture/execution-modes.md (focus on Mode 3 and the
  artifact-destination paragraphs)
- docs/architecture/security-tiers.md (Tier-2 covers artifact creds)
- docs/superpowers/specs/2026-04-25-sealed-i-profile-management-design.md
  (artifact creds are part of Sealed Identity, not a parallel system)

Scope:
- New `ArtifactDestination` model: `{type, target, env_keys}`
    type ∈ {github, s3, local}
    target = repo URL / bucket+prefix / host path
    env_keys = list of Sealed-injected env names the destination
               consumer reads (e.g. ['GITHUB_TOKEN'])
- Per-workflow config: `WorkflowDef.artifact_destination`
- Per-run override: enqueue kwarg
- Tool runner runs the destination upload after CLI agent completes:
    github → git push using GITHUB_TOKEN from sandbox env
    s3     → aws s3 sync using AWS_* from sandbox env
    local  → cp to host-mounted path
- Admin UI: per-workflow page gets an "Artifact destination" card
  with type picker + target input + Sealed env-key allowlist
- Audit: emit `artifact.uploaded` with type + target + bytes + duration

Out of scope:
- New artifact destination types (this thread does the 3 above; new
  ones are additive)
- Cross-run artifact reuse / versioning (future)

Verification:
- Mode 3 workflow can push to a GitHub repo, S3 bucket, or local path
- Audit events surface in /sealed/audit
- Existing Mode 0/1/2 workflows unaffected

Spec to: docs/superpowers/specs/YYYY-MM-DD-plan-art-artifact-destination-design.md
Plan to: docs/superpowers/plans/YYYY-MM-DD-plan-art-artifact-destination.md
```

**Pre-reads:** execution-modes.md + security-tiers.md + Sealed-i spec.

**Estimated size:** 2 days (~10-15 task plan). Small, contained.

---

## Thread 5 — Sealed-iii.B + iii.D (redaction + per-key ACL)

**Goal:** complete the iii hardening tier's data-protection layer. iii.B scrubs Tier-2 secret values from RPC traffic / CLI output / logs. iii.D enforces a per-CLI-tool secret allowlist within an Identity. Both share the tool runner ↔ host RPC seam, so combine.

**Prompt:**

```
You are working on `sagewai/platform`, branch `feat/sealed-iii-b-d`
off latest main.

Read first, in order:
- docs/architecture/security-tiers.md (focus on "Tier-2 plaintext never
  crosses the worker host" promise — that's iii.B's job to enforce)
- docs/architecture/execution-modes.md (Mode 3+ for both)
- docs/superpowers/specs/2026-04-25-sealed-i-profile-management-design.md
- docs/superpowers/specs/2026-04-25-sealed-iii-a-revocation-design.md
  (both are extended, not modified, by iii.B/iii.D)

This is two sub-projects in one PR — sister features sharing the RPC
boundary. Spec each separately, plan each separately, implement
sequentially with a checkpoint commit between.

Sealed-iii.B — Prompt + tool-output redaction:
- Build a redaction filter that runs at the RPC boundary between
  tool runner (sandbox) and Sagewai Agent (host)
- Redacts: any string in the run's effective_secret_keys VALUES
  (not names) before they are written to logs / postgres / streamed
  back to the agent
- Output: replace value with `<redacted:KEY_NAME>` placeholder
- Source-of-truth for values: the running sandbox's os.environ
  (the only place plaintext lives) — the redactor reads them once
  at sandbox start and uses them as a list of forbidden substrings
- Performance: O(n) string scan per RPC payload; acceptable
- Audit: emit `redaction.match` event when a value is found
  (informational — high count = sloppy CLI agent)
- Tests: feed a payload with a known secret value, assert it's
  replaced; assert names are NOT replaced; assert near-misses
  (substring of secret) don't false-positive on shared prefixes

Sealed-iii.D — Per-key ACL:
- Profile gains an `acl: dict[cli_name, list[secret_key_names]]`
  field: which CLI agents can read which secret_keys from this
  profile
- Tool runner reads acl on CLI subprocess spawn; sets only the
  whitelisted env vars on the subprocess (not the full sandbox env)
- Default if absent: cli sees all profile secret_keys (current behavior)
- Admin UI: profile detail page gets a "Per-CLI access" matrix
  (CLIs as rows, secret_keys as columns, checkbox per cell)
- Audit: emit `acl.enforced` when a CLI subprocess is spawned with
  a restricted env subset

Verification:
- A test workflow puts ANTHROPIC_API_KEY + OPENAI_API_KEY in a profile,
  ACL: claude-code can read ANTHROPIC, codex can read OPENAI, neither
  can read the other → spawned subprocess only sees the allowed key
- Redaction: a tool that prints its env produces redacted output in
  the audit log
- Existing Sealed audit events still emitted

Spec to: docs/superpowers/specs/YYYY-MM-DD-sealed-iii-b-redaction-design.md
       + docs/superpowers/specs/YYYY-MM-DD-sealed-iii-d-acl-design.md
Plan:    one plan covering both sub-projects, with a natural split
       point at the iii.B → iii.D boundary
```

**Pre-reads:** security-tiers + execution-modes + Sealed-i + Sealed-iii.A specs.

**Estimated size:** 4 days (~25-task plan, two sub-projects).

---

## Thread 6 — Sealed-iii.C replay safety

**Goal:** durable workflow replays use original-run injection state, not current-state re-resolution. Ensures replays are deterministic and don't fail because a key got revoked or rotated since the original run.

**Prompt:**

```
You are working on `sagewai/platform`, branch `feat/sealed-iii-c-replay`
off latest main.

Read first, in order:
- docs/architecture/runtime-topology.md (replay flows through worker)
- docs/architecture/execution-modes.md (replay must reproduce the
  original step's mode, identity, AND injected key set — not the
  current-state version)
- docs/superpowers/specs/2026-04-25-sealed-i-profile-management-design.md
- docs/superpowers/specs/2026-04-25-sealed-iii-a-revocation-design.md
  (replay sees revocation as a hard failure for now in iii.A; iii.C
  changes that to "use original injection")
- packages/sdk/sagewai/core/state.py — DurableWorkflow + WorkflowRun
  define how replay works today

Scope:
- Persist on each step: original `effective_env_keys`, original
  `secret_key → revocation_id` snapshot, step-completion timestamp
- On replay: skip cascade re-resolution; load the persisted snapshot
  and inject using THOSE keys + values
- Handle the edge: a key in the snapshot is now revoked
  → replay still proceeds (uses snapshot, not current registry)
  → emit `replay.used_revoked_snapshot` audit warning
- Handle the edge: a key in the snapshot has been rotated to a new
  value → replay uses the OLD value if still in the backend's history,
  fails-loud-with-clear-error if not
- The "snapshot" is small: just key NAMES (values are re-fetched
  from the backend at replay time, like normal injection); the
  snapshot's job is to skip the cascade re-resolution
- Replay ergonomics: `WorkflowRun.replay_from(step_index)` API on
  the admin UI run-detail page
- Audit: `replay.started`, `replay.snapshot_loaded`,
  `replay.used_revoked_snapshot`, `replay.completed`

Out of scope:
- Time-travel replay (replay a run as it would have executed at
  some past time T) — that's a much bigger feature
- Cross-version replay (workflow code changed since original run) —
  fail-loud, recommend re-enqueue

Verification:
- Replay a Mode 3 run that succeeded, confirm output identical
  (or near-identical given LLM nondeterminism)
- Revoke a key the original run used, replay → audit warns but
  succeeds
- Rotate a profile, replay an earlier run → replay uses old values
  if backend supports value-history (Sealed-i: builtin doesn't,
  rotate replaces; Vault/SOPS: depends on backend retention)

Spec to: docs/superpowers/specs/YYYY-MM-DD-sealed-iii-c-replay-design.md
Plan to: docs/superpowers/plans/YYYY-MM-DD-sealed-iii-c-replay.md
```

**Pre-reads:** runtime-topology + execution-modes + Sealed-i + Sealed-iii.A specs + DurableWorkflow code.

**Estimated size:** 3 days (~18-task plan).

---

## Thread 7 — Sealed-ii external Identity backends (decompose first)

**Goal:** add Identity backend implementations for Vault / 1Password / AWS SM / SOPS / Bitwarden so operators can adopt Sagewai without storing secrets in `~/.sagewai/profiles.json`. This is a meta-thread that decomposes into 5 sub-projects, then implements the highest-priority one (Vault).

**Prompt:**

```
You are working on `sagewai/platform`. This is a brainstorm + decompose
+ first-implementation thread.

Read first, in order:
- docs/architecture/security-tiers.md
- docs/architecture/execution-backends.md (focus on "Identity backends
  (ProfileBackend)" — the Protocol + scheme/URI model)
- docs/superpowers/specs/2026-04-25-sealed-i-profile-management-design.md
  (the ProfileBackend Protocol they implement)

Two parts:

Part A — Decomposition spec:
- Open spec at docs/superpowers/specs/YYYY-MM-DD-sealed-ii-backends-decomposition.md
- Identify per-backend characteristics:
    Vault:        URL, token / approle / k8s auth, KV v2 path layout
    1Password:    Connect API, vault, item layout, service account
    AWS SM:       region, IAM role, Secret naming pattern (KMS)
    SOPS:         git repo URL, age/PGP key, file layout, polling vs webhook
    Bitwarden:    Secrets Manager API, organization, project, access tokens
- For each: what's read/write/rotation behavior; what auth flow is
  feasible; what's the master-key story (most don't need one — backend
  has its own root key); what audit hooks are available
- Decide priority order. Recommended: Vault first (most common in
  serious ops shops), then SOPS (GitOps-friendly), then AWS SM
  (AWS-native shops), then 1Password / Bitwarden (per-tenant SaaS)
- Each gets its own sub-spec + plan + PR; this thread implements
  Vault only

Part B — Implement Vault backend (Sealed-ii.Vault):
- New `VaultBackend` implementing ProfileBackend Protocol
- URI scheme: `vault://` with path = KV v2 path
- Auth: token from env, AppRole, or k8s service account
- Profile storage: one Vault KV item per profile, structure mirrors
  ProfileWritePayload
- Rotation: not applicable at backend level (Vault has its own
  rotation primitives — operators use them externally)
- Admin UI: "Vault" panel in /sealed/status, shows Vault address,
  auth method, profile count
- Tests: one unit test (stubbed Vault client), one integration test
  (gated on VAULT_ADDR + VAULT_TOKEN — local dev Vault in docker
  compose for the test fixture)

Verification:
- Profile CRUD via REST works with `vault://path` scheme
- Sealed cascade can mix Builtin + Vault profile refs
- Sealed-iii.A revocation still works (revocations stay in postgres;
  Vault is just the value source)
- Existing Builtin tests untouched

Spec(s) to: docs/superpowers/specs/YYYY-MM-DD-sealed-ii-backends-decomposition.md
          + docs/superpowers/specs/YYYY-MM-DD-sealed-ii-vault-design.md
Plan to:    docs/superpowers/plans/YYYY-MM-DD-sealed-ii-vault.md
```

**Pre-reads:** security-tiers + execution-backends + Sealed-i spec.

**Estimated size:** 4 days (~25-task plan including the decomp).

---

## Thread 8 — Docs site + commercial site + client libraries (parallel sub-threads OK)

**Goal:** propagate the new architecture to user-facing surfaces. This is three independent surfaces, each can be its own session.

**Prompt for sub-thread 8a — Sagewai docs site (apps/docs in this repo):**

```
You are working on `sagewai/platform`, branch
`docs/site-architecture-update` off latest main.

Read first:
- docs/architecture/runtime-topology.md
- docs/architecture/security-tiers.md
- docs/architecture/execution-modes.md
- docs/architecture/execution-backends.md
- apps/docs/CLOUDFLARE.md (deploy model: Cloudflare polls main on push)

Goal: bring the user-facing docs site at apps/docs/ in line with the
new architecture. Specifically:

- Rewrite the "Architecture" section of the docs site to mirror
  the four architecture docs. Keep tone user-facing (less internal
  jargon) but the model identical. Cross-reference the canonical
  internal docs.
- Add a new "Execution modes" page — explain when an end user
  picks each mode. Use the worked example from execution-modes.md
  but rephrase for end-user audience.
- Add a new "Sandbox backends" page — operator-facing decision
  guide for picking Docker / K8s / Lambda.
- Update the "Quickstart" page to show a Mode 3 workflow as the
  canonical example (build a portfolio site with Claude Code).
  Older quickstart (just Tier-1 LLM) becomes a separate "minimal
  setup" page.
- Update navigation. Search index.

Build:
- `cd apps/docs && pnpm build`
- Cloudflare auto-deploys from main (per CLOUDFLARE.md), so once
  this PR merges the docs site updates within minutes.

Don't:
- Don't introduce new branding. Don't link to external blogs.
- Don't add marketing language.

PR: `docs(site): architecture update for Modes 0-3b + sandbox backends`
```

**Prompt for sub-thread 8b — Commercial site (separate repo `sagewai/web`):**

```
You are working on `sagewai/web` (the marketing site at sagewai.ai).
This is a SEPARATE git repo from `sagewai/platform`.

Clone it, branch off main: `git clone git@github.com:sagewai/web.git`
then `cd web && git checkout -b update/architecture-positioning`.

Read (in the platform repo's docs):
- platform/docs/architecture/runtime-topology.md
- platform/docs/architecture/security-tiers.md
- platform/docs/architecture/execution-modes.md
- platform/docs/architecture/execution-backends.md

Goal: update the marketing site's positioning to reflect the
clarified architecture. Specifically:

- "How it works" page or section: replace any "Sagewai runs your
  AI agents" generic copy with the clearer "Sagewai orchestrates
  CLI agents (Claude Code, Codex, Gemini) inside identity-isolated
  sandboxes — your customers' credentials never touch our control
  plane" framing.
- Add or update a "Security" page that surfaces the Tier-1 / Tier-2
  split, the audit trail, the revocation primitives. End-user
  focused, no implementation detail. Cite the public docs site as
  the technical reference.
- "Use cases" page: lead with Mode 3 examples (build a website,
  write a report, ship code) since that's the differentiator.
  Mode 0/1/2 are mentioned but not foregrounded.
- Hero copy: needs to reflect the moat — "isolated identities per
  customer" / "enterprise-ready secret management" / "audit-first
  by design" — pick whichever phrasing fits the brand voice.

Build: standard Next.js / Cloudflare Workers Builds.
The site auto-deploys from main on push.

Don't:
- Don't promise features that aren't shipped (Sealed-ii / Sealed-v
  / Mode 3b / K8s backend are all forward-looking — list as roadmap,
  not "today").
- Don't claim certifications Sagewai doesn't have (SOC2, ISO27001,
  etc.) unless someone formally has them.

PR: `update: architecture positioning for Sagewai 1.0 (Modes + Sealed)`
```

**Prompt for sub-thread 8c — Client libraries (17 wrapper repos):**

```
You are working on the 17 sagewai/sagewai-* client wrapper repos
(thin SDK wrappers in TS, Go, Rust, Java, Python, etc.).

The `sagewai/platform` repo is the source of truth. Each client
library is a thin wrapper that exposes the same WorkflowRun lifecycle
+ enqueue API.

Read first (in the platform repo):
- docs/architecture/runtime-topology.md
- docs/architecture/security-tiers.md
- docs/architecture/execution-modes.md (focus on Mode 0-3b — these
  are the modes the client API surfaces)
- packages/sdk/sagewai/core/state.py (DurableWorkflow + WorkflowRun
  shape — clients mirror this)

Goal: each client library needs three updates:

1. README: cite the platform's architecture docs. Explain that the
   client wrapper does not execute work — it submits enqueue requests
   to a Sagewai control plane and queries run status. Emphasise the
   security model (Tier-1 vs Tier-2; clients never hold Tier-2
   secrets).

2. API surface: ensure the enqueue method accepts:
     - input_data
     - execution_mode: ExecutionMode (or per-language equivalent)
     - security_profile_ref: str (optional)
     - artifact_destination: ArtifactDestination (optional)
   Some wrappers may not have all of these yet — add what's missing,
   maintain backward compat.

3. Examples: at least one Mode 0 example (planning) + one Mode 3
   example (CLI agent). Other modes mentioned in README only.

Recommended approach: ONE master tracking issue / PR in
sagewai/platform titled `[meta] update client libraries for Sagewai
1.0 architecture` that links to per-repo PRs. Each per-repo PR is
a focused session.

Per-repo branches: `update/sagewai-1.0-architecture`.

Don't:
- Don't introduce breaking API changes without a major version bump.
  If the new fields require breaking changes, schedule v1.0 release
  and document migration.
- Don't reimplement workflow execution in the client. Clients are
  thin RPC wrappers; the platform is the executor.
```

**Pre-reads:** the 4 architecture docs + relevant existing repo content.

**Estimated size:** 8a/8b ~1 day each; 8c ~3-5 days (17 repos × ~30min each = ~10h, plus the master tracking).

---

## Thread 9 — Sealed-iv (Mode 3b: HITL + JIT credentials)

**Goal:** the bidirectional callback channel + policy engine for runtime credential requests.

**Prompt:**

```
You are working on `sagewai/platform`, branch `feat/sealed-iv-jit-hitl`
off latest main.

Read first, in order:
- docs/architecture/runtime-topology.md (focus on Mode 3b in the
  topology diagram — the bidirectional channel)
- docs/architecture/execution-modes.md (Mode 3b section in particular)
- docs/architecture/security-tiers.md
- docs/superpowers/specs/2026-04-25-sealed-i-profile-management-design.md
- docs/superpowers/specs/2026-04-25-sealed-iii-a-revocation-design.md

This is the biggest Sealed sub-project after Sealed-i. Brainstorm
+ spec + plan + implement carefully.

Scope:
- Tool runner gains a callback RPC channel (sandbox → host)
- Sagewai Agent on host gains a policy engine that evaluates
  credential requests:
    Allow rules:    "auto-approve any credential matching pattern X"
    Deny rules:     "always deny escalations to admin roles"
    HITL rules:     "for credential X, require operator approval
                     via the admin UI; surface with timeout T"
- Policy storage: per-system, per-workflow, per-profile (cascade)
- Admin UI: pending HITL requests page; approve/deny with reason
- JIT injection: approved credentials are env-injected into the
  running sandbox (Docker: docker exec env-set; K8s: pod env update
  via projected secret reload; Lambda: re-invoke not feasible →
  Mode 3b not supported on Lambda, document)
- Audit: `credential.requested`, `credential.approved`,
  `credential.denied`, `credential.delivered`,
  `credential.hitl_timeout`
- Revocation interaction: hard-revoke during a HITL gate cancels
  the gate

Out of scope:
- Inter-run policy state (a request approved in run R doesn't
  auto-approve in run S — every request is independent)
- Workflow-level "always-allow-for-this-wf" (use cascade overrides
  for that)

Verification:
- A Mode 3 workflow's CLI agent requests a new credential not in
  its profile → host policy evaluates → operator approves → cred
  injected → CLI continues
- Same workflow, same request, but policy is "deny" → CLI gets
  error, audit captures both events
- Same workflow, HITL policy, operator does nothing for T seconds
  → request times out, CLI gets denied error

Spec to: docs/superpowers/specs/YYYY-MM-DD-sealed-iv-jit-hitl-design.md
Plan to: docs/superpowers/plans/YYYY-MM-DD-sealed-iv-jit-hitl.md
```

**Pre-reads:** runtime-topology + execution-modes + security-tiers + Sealed-i + Sealed-iii.A specs.

**Estimated size:** 5 days (~30-task plan).

---

## Thread 10 — Sealed-v reactive directives (the second moat)

**Goal:** runtime mode promotion based on signals — the autopilot can promote a Mode 1 step to Mode 2 (add Identity) or Mode 2 to Mode 3 (add CLI agent) if it detects the step needs it.

**Prompt:**

```
You are working on `sagewai/platform`, branch `feat/sealed-v-reactive`
off latest main.

This is the largest pending Sealed sub-project. Sealed-v is the
"second moat" — it's where Sagewai's autopilot meets the security
model. Plan accordingly: brainstorm extensively, possibly decompose.

Read first:
- docs/architecture/runtime-topology.md
- docs/architecture/execution-modes.md (the spectrum: 0 → 3b)
- docs/architecture/security-tiers.md
- docs/superpowers/specs/2026-04-25-sealed-i-profile-management-design.md
- docs/superpowers/specs/2026-04-25-sealed-iii-a-revocation-design.md
- existing autopilot code: packages/sdk/sagewai/autopilot/

Conceptual scope:
- Reactive directives are policy + signal handlers that mutate
  a workflow's per-step mode at runtime
- Signal sources:
    cost overrun:     "this Mode 3 step has consumed too many tokens,
                       cancel + downgrade to Mode 2"
    capability gap:   "this Mode 1 step requires a credential it
                       doesn't have, promote to Mode 2 with a profile"
    risk escalation:  "this Mode 0 step is calling shell commands,
                       sandbox it (promote to Mode 1)"
    rotation drift:   "the profile this run is using has rotated;
                       restart with the new identity"
- Directives ALWAYS audit. Every promotion / demotion / abort is
  observable.
- Directives are configurable via admin UI (autopilot policy page)
- Directives compose with Sealed-iv (HITL on policy changes)

Why this is "the moat":
- Other agent platforms force the operator to pre-configure every
  detail; Sagewai can RUN intelligence about its OWN execution
- Combined with Sealed (per-customer isolation) + the tier model
  (no host-side leakage) + reactive policy, the result is
  "self-tuning, self-auditing, self-enforcing" — properties no
  competitor has

This thread should produce:
- A high-level design spec covering the directive engine
- Decomposition into sub-projects (probably 3-5: signal sources,
  directive evaluator, policy storage, admin UI, replay safety)
- Implementation of the FIRST sub-project (signal-source framework)

Other sub-projects spawn separate threads.

Spec to: docs/superpowers/specs/YYYY-MM-DD-sealed-v-reactive-decomposition.md
Plan to: docs/superpowers/plans/YYYY-MM-DD-sealed-v-signal-sources.md
```

**Pre-reads:** all 4 architecture docs + Sealed-i + Sealed-iii.A specs + autopilot code.

**Estimated size:** 2 weeks for the full series (multiple sub-threads).

---

## Thread queue summary

```
Thread 1   (Mode-aware runner)            ─► ✅ SHIPPED (PR #155); per-step mode is a follow-up
Thread 1.5 (Pre-v1.0 cleanup audit)       ─► PARALLEL with everything; blocks v1.0 release
Thread 2   (Plan 1.5 pooling)             ─► after 1
Thread 3   (Plan SBX-K8S)                 ─► after 2
Thread 4   (Plan ART)                     ─► parallel after 1
Thread 5   (Sealed-iii.B + iii.D)         ─► parallel after 1
Thread 6   (Sealed-iii.C replay)          ─► parallel after 1
Thread 7   (Sealed-ii Vault first)        ─► parallel after 1
Thread 8a  (apps/docs)                    ─► parallel anytime
Thread 8b  (sagewai/web)                  ─► parallel anytime
Thread 8c  (client libraries)             ─► parallel anytime
Thread 9   (Sealed-iv Mode 3b)            ─► parallel after 1
Thread 10  (Sealed-v reactive)            ─► parallel after 1
Thread F   (Plan SBX-LAMBDA)              ─► optional, anytime — niche
```

Threads 1 → 2 → 3 are sequential because each builds on infrastructure the prior thread lands. All others can fan out in parallel after Thread 1 lands.

Thread 1.5 (cleanup audit) runs in parallel with everything. It's a series of small PRs against the current main branch; whatever feature work is in flight just rebases over it as merges land. The audit blocks v1.0 because honest release notes require knowing what's actually shipped.

Thread 8 sub-threads (8a/8b/8c) and Thread F (Lambda) have no upstream blocker — they can be picked up at any time.

---

## Suggested kickoff sequence for next 2-3 weeks

If you have ~2-3 calendar weeks of capacity:

| Day | Thread | Why |
|---|---|---|
| 1 | Thread 1 (mode-aware runner refactor) ✅ shipped PR #155 | unblocked the rest |
| 1-3 (parallel) | Thread 1.5 Phase 0 (cleanup inventory) | produces tracking issue; subsequent phases run alongside everything |
| 2-4 | Thread 2 (Plan 1.5 pooling) | unlocks production performance |
| 2-3 (parallel) | Thread 8a (docs site) | quick win, propagates the architecture publicly |
| 4-7 (parallel) | Thread 1.5 Phases 1-3 (stale tests, dead code, hallucinated code) | low-risk deletions while feature work continues |
| 5-8 | Thread 3 (Plan SBX-K8S) | production-deployment unlock |
| 5-6 (parallel) | Thread 4 (Plan ART) | makes Mode 3 actually useful |
| 8-11 (parallel) | Thread 1.5 Phases 4-6 (obsolete patterns, dup logic, stale docs) | medium-risk consolidation |
| 9-12 | Thread 5 (Sealed-iii.B + iii.D) | hardening tier completion |
| 9-10 (parallel) | Thread 8b (commercial site) | go-to-market alignment |
| 12-14 (parallel) | Thread 1.5 Phases 7-8 (abandoned experiments, stale CI) | final polish before tag |
| 13-15 | Thread 6 (Sealed-iii.C replay) | production reliability |
| 16+ | Threads 7, 9, 10, 8c, F | per priority |

The cleanup audit (1.5) is a continuous low-priority background task running for the whole window. By day 14-15 the codebase should be tight enough to declare v1.0 with honest release notes.
