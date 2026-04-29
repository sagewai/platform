# Sagewai execution modes

**Status:** authoritative
**Last revised:** 2026-04-25
**Companion docs:** [runtime-topology.md](runtime-topology.md), [security-tiers.md](security-tiers.md), [execution-backends.md](execution-backends.md)

This document fixes the five execution modes a workflow step can run in. Modes are per-step, not per-deployment. Picking the right mode per step is what makes Sagewai both efficient and safe.

---

## The five modes at a glance

| Mode | Worker | Sandbox | Identity | CLI agent(s) | Artifact dest | Best for |
|---|:---:|:---:|:---:|:---:|:---:|---|
| **0 — Bare** | ✓ | — | — | — | — | Pure orchestration: planning, summarising, simple Q&A, lightweight transforms |
| **1 — Sandboxed** | ✓ | ✓ | — | — | — | Untrusted tool execution with no per-customer creds (e.g. run user's Python snippet) |
| **2 — Identity** | ✓ | ✓ | ✓ | — | optional | Tool execution with customer creds (read their S3, query their DB, call their API) |
| **3 — Full** | ✓ | ✓ | ✓ | ✓ | ✓ | Real user task: build a website, fix a bug in a repo, generate a report and push it |
| **3b — Full + JIT callback** | ✓ | ✓ | ✓ + callback | ✓ | ✓ | Like 3, but CLI agent can request credentials it doesn't have at runtime (Sealed-iv) |

**The mode is selected per workflow step**, not once for the whole workflow. A workflow that "reads a brief, builds a site, summarises what it did" will typically be Mode 0 → Mode 3 → Mode 0.

---

## Mode 0 — Bare

The simplest mode. The worker process executes the step directly; no isolation, no sandbox, no identity injection.

**Topology:**
```
Worker process
  └── Sagewai Agent runs the step inline
        └── reads inputs from postgres
        └── calls Tier-1 LLM if needed (worker env keys)
        └── writes output to postgres
```

**Cost:** essentially free (no container start, no network bridging, no env injection).

**Security:** the step has full access to the worker host process. Anything the worker can do, the step can do. **Use only for code Sagewai itself trusts** — never for code the user wrote or untrusted CLI invocations.

**Examples:**
- Workflow step "summarise the previous step's output in plain English" — reads postgres, calls Tier-1 LLM, writes summary.
- Step "decide which Mode 3 CLI to dispatch next based on input shape" — pure planning.
- Step "validate the user's input against a JSON schema" — no IO, no LLM.

**When NOT to use Mode 0:**
- The step calls user-provided code, scripts, or shell commands.
- The step needs access to customer credentials.
- The step calls external APIs that should be rate-limited or audited per customer.

---

## Mode 1 — Sandboxed (no identity)

The step runs in a sandbox container with empty env. Useful for executing code Sagewai doesn't trust, but where no customer credentials are needed.

**Topology:**
```
Worker process
  └── Sagewai Agent
        └── acquires sandbox (Docker/K8s/Lambda) with EMPTY env
        └── dispatches tool execution via tool runner RPC
        └── writes output to postgres
```

**Cost:** sandbox start (~2-8s cold; ~50-100ms warm with Plan 1.5 pooling).

**Security:** isolation is the trust boundary. Network policy applies (NONE / EGRESS_ONLY / FULL). Filesystem is container-local; nothing persists by default.

**Examples:**
- Run a user-supplied Python snippet, return the result.
- Execute an unverified shell command from a workflow input.
- Run a code linter or formatter on user-provided code.
- Generate a thumbnail with `imagemagick` — needs isolation in case of a bad input file, but no credentials.

**Identity is empty:** the sandbox has no Sealed env injected. Tool runner reads only standard container env (PATH, HOME). Useful when you want isolation for safety, not for credential scoping.

---

## Mode 2 — Identity (no CLI agent)

Mode 1 plus a Sealed Identity is injected into the sandbox env. Tools running inside can read customer credentials and behavior knobs.

**Topology:**
```
Worker process
  └── Sagewai Agent
        ├── resolves Sealed cascade (system + workflow + user)
        │   re-resolves at sandbox-start time (drift detection)
        ├── acquires sandbox; backend injects env from cascade
        └── dispatches tool execution via tool runner RPC
              └── tools read os.environ for creds
```

**Cost:** Mode 1 cost + Sealed cascade resolution (~10-30ms for typical 1-3 level cascade against builtin backend; ~100ms+ for external backends like Vault).

**Security:** all of Mode 1 plus per-customer credential scoping. Audit trail captures every key injected (`profile.injected`), every cascade resolution (`profile.cascade.resolved`), every revocation interaction. No CLI agent → no LLM keys leave the sandbox unless a generic tool calls one.

**Examples:**
- Step "query customer's PostgreSQL database, return summary" — needs `CUSTOMER_DB_URL` from Sealed.
- Step "fetch from customer's S3 bucket and process" — needs `AWS_ACCESS_KEY_ID/SECRET`.
- Step "call customer's internal API with their auth token" — needs `CUSTOMER_API_TOKEN`.

**When to choose Mode 2 over Mode 3:** when the work is deterministic and well-bounded. CLI agents (Mode 3) are for open-ended tasks where the LLM needs to make choices about *what* to do. Mode 2 is for "execute this specific operation with the right credentials."

---

## Mode 3 — Full (CLI agent)

Mode 2 plus the tool runner spawns a CLI agent (Claude Code, Codex, Gemini, custom) inside the sandbox. The CLI does the actual work; Sagewai Agent on host orchestrates.

**Topology:**
```
Worker process
  └── Sagewai Agent (host)
        ├── decides which CLI to invoke + with what prompt
        ├── acquires sandbox with Identity (Tier-2 keys + artifact creds)
        ├── dispatches via tool runner RPC:
        │     "claude-code-cli run --prompt='build portfolio site'
        │      --workdir=/workspace"
        └── streams stdout/stderr back from sandbox

Sandbox
  ├── Identity env: ANTHROPIC_API_KEY, GITHUB_TOKEN, …
  ├── tool runner spawns CLI as subprocess
  └── CLI:
        ├── reads ANTHROPIC_API_KEY from os.environ
        ├── calls Anthropic API directly (network egress from sandbox)
        ├── edits files in /workspace
        └── on completion: pushes to artifact destination
              (git push using GITHUB_TOKEN, or aws s3 sync, or cp)
```

**Cost:** Mode 2 cost + CLI agent runtime (varies — Claude Code on a complex prompt can take 5-30 minutes). LLM API costs are charged to the CLI's Tier-2 key, not Sagewai's Tier-1.

**Security:** the CLI runs inside the sandbox boundary. Its LLM keys are sandbox-only. Its filesystem access is `/workspace` (or wherever the image variant configures). Network policy applies — typically `EGRESS_ONLY` to LLM provider + artifact destination.

**Examples:**
- "Build a portfolio website from this brief" → Claude Code
- "Refactor this Python module for performance" → Codex or Claude Code
- "Generate a marketing landing page in Next.js" → Gemini CLI or Claude Code
- "Run linters + tests + open a PR with fixes" → Codex

**Image variants matter here.** The sandbox image determines which CLI agents are available. The variant catalog (`sagewai/sandbox-claude-code`, `sagewai/sandbox-multi`, etc.) is operator-curated; workflows declare which variant they need.

**Artifact destinations are first-class.** Mode 3 implies output. Where the CLI's work goes is configured per-workflow:
- `github` — `git push` to a target repo, using `GITHUB_TOKEN` from Identity
- `s3` — `aws s3 sync /workspace s3://bucket/path`, using AWS creds from Identity
- `local` — `cp /workspace /host-mounted/path`, target path passed by worker
- `none` — destination is the workflow output (`/workspace` is read by Sagewai Agent and persisted to postgres on step completion)

---

## Mode 3b — Full with JIT credential callback

Mode 3 plus a bidirectional channel: the CLI agent or tool runner can request credentials it doesn't have at runtime, and the Sagewai Agent on host evaluates against policy (auto-approve, deny, HITL).

**This mode is Sealed-iv territory.** The tool runner RPC channel becomes bidirectional: in addition to host→sandbox dispatch, sandbox→host credential requests are honoured.

**Topology (additional flow on top of Mode 3):**
```
Sandbox (CLI agent or tool runner)
  └── needs credential X not in current env
        ↓ callback
        request_credential(name="ANOTHER_REPO_TOKEN",
                           scope="github:push",
                           reason="user said push to repo Y")
        ↓
Worker process: Sagewai Agent
  └── policy engine evaluates request:
        ├── auto-approve (matches a policy rule)
        ├── deny (forbidden by policy)
        └── HITL (requires operator approval via admin UI)
        ↓ if approved:
  └── Sealed cascade lookup or dynamic creation
        ↓ inject value into running sandbox env
  └── audit emit: credential.requested → approved → delivered
```

**Cost:** Mode 3 cost + per-callback round-trip (sandbox ↔ host RPC) + policy evaluation (~ms) or HITL approval delay (operator-bounded).

**Security:** every callback is audit-logged. The injection mechanism (e.g., `docker exec env-set`, k8s pod ENV update via projected secret reload, Lambda env update via reinvocation) limits blast radius — the new credential is in this sandbox only, not propagated to siblings in the pool.

**Examples:**
- Claude Code is asked to push to a repo not pre-configured. Requests `GITHUB_TOKEN_REPO_X`. Operator policy: "auto-approve writes to repos under our org." → injected.
- Codex needs to call a third-party API the operator hasn't pre-authorised. Requests `THIRD_PARTY_API_KEY`. Policy: "HITL required for new API keys." → admin UI surfaces the request; operator approves; injected.
- A tool needs to escalate to a more powerful AWS role. Requests `AWS_ROLE_ARN_X`. Policy: "deny escalations to admin roles." → denied.

**When to use 3b over 3:** when the workflow is open-ended enough that you can't enumerate all credentials at enqueue time. For closed workflows ("build a site, push to repo X"), use Mode 3 with X's creds pre-set.

---

## Decision tree: which mode for which step?

```
Is the step pure orchestration (planning, summarising, validation)?
  └── YES → Mode 0
  └── NO ↓

Does the step need customer-specific credentials?
  ├── NO → Does the step run untrusted code or call shell/tools?
  │         ├── NO  → Mode 0 (just trusted Sagewai code)
  │         └── YES → Mode 1
  └── YES ↓

Does the step invoke a CLI agent (Claude Code, Codex, Gemini, …)?
  ├── NO  → Mode 2
  └── YES ↓

Are all credentials needed at start known at enqueue time?
  ├── YES → Mode 3
  └── NO  → Mode 3b
```

---

## Cost / security / capability trade-offs

```
       ┌────────────────────────────────────────────────────┐
       │                                                    │
       │  Mode 0 ────────────────────────────────► Mode 3b  │
       │                                                    │
       │  cheaper                              more capable │
       │  faster                            more isolated   │
       │  less secure                       more auditable  │
       │  fewer features                    more complex    │
       │                                                    │
       └────────────────────────────────────────────────────┘
```

| Dimension | Mode 0 | Mode 1 | Mode 2 | Mode 3 | Mode 3b |
|---|---|---|---|---|---|
| Step latency overhead | ~ms | container start (sec; <100ms pooled) | + cascade resolution (~tens of ms) | + CLI startup (varies) | + callback latency |
| LLM cost paid by | Tier-1 (operator) | n/a (no LLM by default) | n/a or operator-side tools | Tier-2 (customer) | Tier-2 (customer) |
| Customer credentials accessible | no | no | yes (env-injected) | yes (env-injected) | yes (env + JIT) |
| Network isolation | host network | sandbox network policy | sandbox network policy | sandbox network policy | sandbox network policy |
| Filesystem isolation | host fs | container fs | container fs + identity | + /workspace + artifact dest | + JIT creds |
| Sealed audit coverage | n/a | n/a | full | full | full + callback events |
| Replay-determinism (Sealed-iii.C) | trivial (pure code) | sandbox env = empty (trivial) | replay original Identity values | replay original Identity + CLI prompts/responses | replay original + cached callback results |

---

## Mixing modes within a workflow

Real workflows almost always mix modes. Today there are two layers:

- **Run-level `execution_mode` (shipped, PR #155).** Every `WorkflowRun` carries an `ExecutionMode` field — `BARE` / `SANDBOXED` / `IDENTITY` / `FULL` / `FULL_JIT` — set on `enqueue` and persisted via migration `005_execution_mode`. The worker reads this on dispatch. The legacy `requires_sandbox_mode` column (Plan 3a's `SandboxMode` enum) is kept for back-compat — it still drives the fleet's worker-capability matching predicates — and is now **derived** from `execution_mode` at enqueue (BARE → NONE; everything else → PER_RUN).
- **Per-step mode (future).** The decorator API below is illustrative of where this is going. When per-step mode lands, a single workflow run will carry a different mode per step.

Today's run-level API:

```python
from sagewai.core.state import ExecutionMode

run_id = await workflow.enqueue(
    input_data={"brief": "..."},
    execution_mode=ExecutionMode.FULL,        # Mode 3
    security_profile_ref="customer-portfolio",
)
```

The future per-step decorator API:

```python
@workflow.step("plan")          # Mode 0 implicit
async def plan(brief: str) -> dict:
    """Pure orchestration step — runs inline on worker."""
    return await sagewai_agent.plan(brief)

@workflow.step(
    "build_site",
    execution_mode=ExecutionMode.FULL,
    security_profile_ref="customer-portfolio",
    cli_agent="claude-code",
)                                # Mode 3 explicit
async def build_site(plan: dict) -> str:
    """Mode 3 step — CLI agent, identity, artifact dest."""
    return await dispatch_cli(plan)

@workflow.step("summarise")      # Mode 0 implicit
async def summarise(artifact_path: str) -> str:
    """Mode 0 — orchestration."""
    return await sagewai_agent.summarise_changes(artifact_path)
```

The per-step decorator is illustrative — that follow-up plan has not yet been written.

---

## Worked example: "build a customer's portfolio site"

A typical Mode 0 → Mode 3 → Mode 0 pipeline:

```
Step 1 — receive_brief                                       Mode 0
  Input: customer's natural-language brief
  Reads: workflow input
  Tier-1 LLM call: "extract structured requirements"
  Output: JSON {style, sections, target_repo, ...}
  Cost: ~500ms, Tier-1 LLM tokens (cheap planning model)

Step 2 — scaffold                                            Mode 3
  Input: requirements JSON, target repo URL
  Acquires: sandbox (Mode 3, image variant claude-code)
  Identity: profile "portfolio-customer-X" injected:
    ANTHROPIC_API_KEY (Claude Code uses)
    GITHUB_TOKEN (push artifact)
  Tool runner spawns Claude Code:
    claude-code run --prompt="scaffold Next.js site per JSON brief"
  Claude Code calls Anthropic, edits /workspace
  On completion: git push origin main → customer's repo
  Cost: ~5-15min, Tier-2 LLM tokens (Claude Sonnet)

Step 3 — verify                                              Mode 1
  Input: target repo URL (just-pushed commit SHA)
  Acquires: sandbox (Mode 1, image variant base — no creds needed)
  Tool runner runs:
    git clone <url> /tmp/check
    cd /tmp/check && npm ci && npm run build
  Returns: {build: "ok"} or {build: "failed", error: "..."}
  Cost: ~30-60s, no LLM
  No identity needed — the repo is public-readable for the build.

Step 4 — summarise                                           Mode 0
  Input: build result
  Tier-1 LLM call: "write a one-paragraph completion message"
  Output: human-friendly status to webhook / Slack
  Cost: ~500ms, Tier-1 LLM tokens
```

Total: 1 customer LLM bill (Step 2), 0 customer credentials touched outside the sandbox, 4 audit events (`profile.cascade.resolved`, `profile.injected`, `secret.decrypted` × N, `pool.sandbox.reset`).

---

## Anti-patterns

1. **One mode for the whole workflow.** Setting `sandbox_mode=PER_RUN` at the workflow level forces every step into the sandbox, making the cheap orchestration steps (Mode 0) needlessly expensive.

2. **Mode 3 for a Mode 2 task.** If you don't need the CLI agent's open-ended decision-making, don't pay for it. A scheduled "fetch S3, transform CSV, write to DB" job is Mode 2, not Mode 3.

3. **Mode 1 with credentials in workflow input.** "Pass the API key as a step argument" defeats Sealed entirely. Credentials always flow through Identity, never through workflow inputs.

4. **Mode 3b without policy.** Mode 3b's whole point is that the host-side policy decides what's allowed. If your "policy" is "auto-approve everything," you have Mode 3 with extra steps and an attack surface.

5. **Mode 0 for code Sagewai doesn't write.** The worker host is the only thing trusted in Mode 0. User-provided shell commands, plugin code, scripts — all belong in Mode 1+.

---

## Forward dependencies

| Plan | Mode it requires / extends |
|---|---|
| Mode-aware runner refactor — run-level (shipped, PR #155) | All — first-classes the `execution_mode` field on workflow runs |
| Mode-aware runner refactor — per-step (future) | All — moves `execution_mode` to the step level so a single run can mix modes |
| Plan 1.5 (sandbox pooling) | Modes 1+ — pools the sandbox containers |
| Plan ART (artifact destination, shipped) | Mode 3+ — formalises the artifact upload contract; ships GitHub / S3 / local uploaders + admin UI + audit pipeline. CLI-dispatch integration calls `sagewai.artifacts.runtime.apply_artifact_destination` once per-step Mode 3 dispatch lands. |
| Sealed-iii.B (redaction) | shipped (PR pending) — host-side `RedactingSandboxHandle` enforces the Tier-2 invariant |
| Sealed-iii.C (replay safety) | All — replay reproduces step + mode + identity at original-run time |
| Sealed-iii.D (per-key ACL) | shipped (PR pending) — host-side `AclFilteringSandboxHandle` enforces per-CLI Tier-2 allowlist |
| Sealed-iv (HITL + JIT) | Mode 3b — implements the callback channel + policy engine |
| Sealed-v (reactive directives) | All — autopilot can promote a step from Mode 1 → Mode 2 → Mode 3 based on runtime signals |
