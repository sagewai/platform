# Using Sagewai as the work coordinator

Sagewai coordinates bounded software work between you, Codex, Claude,
HarnessRuntime, and a deterministic verifier. You state an outcome and its
limits. Sagewai persists the contract and evidence, assigns write stages to
Codex or an opted-in harness tier, assigns independent read-only analysis and
review to Claude, runs the repository's locked verification contract, and stops
when human attention or control evidence is required.

The durable Work record is authoritative. Neither model's chat history is.

## What Sagewai automates today

The shipped software profile can coordinate a local request or GitHub issue
through analysis, implementation, verification, independent review, bounded
repair, pull request and merge gates, and a verified result. It can resume after
a process or worker disappears without repeating completed stages.
Implementation uses Codex by default; HarnessRuntime is opt-in with
`--prefer-free-implementation`.

The generic Work kernel can host more profiles, but arbitrary business-work
automation is not implied by the current software profile. The Approval Desk
example below is a safe application for Sagewai to modify; it is not a second
control plane or a claim that Sagewai already sends payments or approves orders.

Delivery is optional and not part of core coordination. The delivery contracts
are provider-neutral, but Cloudflare is the only adapter shipped today and is
never selected by default. EKS, ECS, AKS, GKE, or another platform requires a
configured adapter. A successful software run does not imply a deployment.

## 1. Prepare a trusted checkout

Install Sagewai, `just`, Node.js 20 or newer, and the repository's locked Python
toolchain. Install and authenticate the native `codex` and `claude` CLIs on the
machine that will execute them. Sagewai uses their existing local authentication
and does not collect those credentials.

Verification is networkless and runs in a disposable container. Build the
repository's pinned verifier image once on the coordinator machine:

```bash
just work-verifier-build
```

The command runs `just smoke` inside the image with the checkout mounted
read-only and prints an exact `SAGEWAI_WORK_VERIFICATION_IMAGE=sha256:...`
export. Copy that export into the terminal where you run `sagewai work`. The
direct image ID is immutable and local to that machine. Do not place model
credentials or unrelated host secrets in the image.

From this repository, prove the deterministic contract before involving either
model:

```bash
just smoke
```

Use `just test-apps-smoke` when you want only the two example suites.

## 2. Coordinate a bounded local change

Start with one of the tangible applications in [`test-apps`](../../test-apps/).
The following request gives the coordinator an outcome, observable acceptance
criteria, and an explicit scope:

```bash
sagewai work --project coordinator-demo start \
  'In test-apps/browser-game, add a pause control. Pausing must stop keyboard and touch movement without changing score, lives, hazards, or collected signals. Add deterministic tests and change nothing outside that app.'
```

`start` prints a Work ID. Inspect durable state instead of relying on terminal
output from a model:

```bash
sagewai work --project coordinator-demo status WORK_ID
sagewai work --project coordinator-demo pending
sagewai work --project coordinator-demo metrics --work-id WORK_ID
```

If `status` shows unfinished Work after an interruption and `pending` reports
no gate or blocker, resume from the last durable stage:

```bash
sagewai work --project coordinator-demo resume WORK_ID
```

You can stop the process between commands and run `resume` later. The local
example uses the default local route; every resume must use the same execution
selection as `start`. Completed stages retain their receipts, and only the
unfinished stage is eligible to run again.

Try a data-and-policy change next:

```bash
sagewai work --project coordinator-demo start \
  'In test-apps/approval-desk, add an expires_at field. Expired pending requests must be shown separately and cannot be approved. Preserve project isolation and immutable history, add restart-safe tests, and change nothing outside this app.'
```

The application README files contain more narrowly scoped prompts. Use one per
WorkItem so the contract, review, and evidence remain easy to evaluate.

## 3. Respond only when attention is required

`pending` is the canonical attention queue. It may report a gate request, a
blocked question, or degraded control. Use the exact Work and attention IDs it
prints.

For an approval gate:

```bash
sagewai work --project coordinator-demo approve WORK_ID GATE_ID
sagewai work --project coordinator-demo resume WORK_ID
```

Approval authorizes only the named gate. It does not expand the WorkContract or
authorize an unrelated external action. `CONTROL_DEGRADED` freezes new
state-changing actions until authority, observability, or reversibility evidence
is restored; do not bypass it by rerunning a model directly.

## 4. Coordinate a GitHub issue

Use an issue URL when the desired result should follow the issue-to-PR lifecycle.
The token must be authorized only for the intended repository:

```bash
export GITHUB_TOKEN=ghp_...
sagewai work --project coordinator-demo start \
  https://github.com/OWNER/REPOSITORY/issues/123
sagewai work --project coordinator-demo pending
sagewai work --project coordinator-demo approve WORK_ID GATE_ID
sagewai work --project coordinator-demo resume WORK_ID
```

GitHub carries discussion, pull-request, gate, and merge references. Sagewai's
store remains the canonical lifecycle record. A transient GitHub failure does
not justify rerunning completed Codex implementation.

For one deliberately selected intake mechanism, label an issue and run:

```bash
sagewai work --project coordinator-demo intake --label sagewai-work
```

Each invocation starts at most one unseen issue among the oldest 100 open issues
with that label. Re-running intake does not start the same issue twice.

## 5. Observe work in the console

For a first local or two-device Work test, run the backend, Admin UI, and Work
CLI on the same coordinator machine with the same `SAGEWAI_HOME`. This keeps
their SQLite Work and Fleet state shared:

```bash
export SAGEWAI_HOME="$PWD/.sagewai-dev"
just dev-all
```

Open <http://localhost:3008/setup> on a fresh installation, then
<http://localhost:3008/work>. The Work Control Console reads the same backend
state as `status` and `pending`; it does not maintain a browser-owned lifecycle.
Use it to inspect active Work, events, approvals, blocked questions, degraded
control, Evidence Board references, workers, and delivery observations.

Do not use `just compose-up` for this first test: its backend database is inside
the Compose network while the host Work CLI uses its own configured database.

## 6. Put Codex and Claude on a Mac mini and laptop

Use the laptop as the coordinator: it runs the backend, Admin UI, Work CLI, and
the verifier image. Use the Mac mini for Codex and the laptop for Claude. Both
machines need a trusted checkout at the same Git base. Authenticate `codex` only
on the Mac mini and `claude` only on the laptop; Sagewai never sends those native
credentials to the backend.

In Admin, create project `coordinator-demo`, then open
<http://localhost:3008/fleet/enrollment-keys>. Create one key with max uses `2`,
or `3` if you will also register the harness worker below, and allowed pool
`default`. Copy the secret when it appears; it is shown once.

If the backend listens only on laptop loopback, open a tunnel from the Mac mini:

```bash
ssh -N -L 18000:127.0.0.1:8000 YOUR_LAPTOP_USER@YOUR_LAPTOP
```

In a second Mac mini terminal, start its Codex worker through that tunnel:

```bash
export SAGEWAI_ADMIN_URL=http://127.0.0.1:18000
sagewai fleet run --name mac-mini-codex --project coordinator-demo \
  --capabilities runtime.codex,filesystem.write \
  --pool default \
  --enrollment-key 'PASTE_ENROLLMENT_KEY' \
  --work-repository /absolute/path/to/platform \
  --codex-model gpt-5.6-sol \
  --codex-reasoning-effort ultra
```

On the laptop, keep `just dev-all` running and start the Claude worker in another
terminal with the same `SAGEWAI_HOME`:

```bash
export SAGEWAI_HOME=/absolute/path/to/platform/.sagewai-dev
export SAGEWAI_ADMIN_URL=http://127.0.0.1:8000
sagewai fleet run --name laptop-claude --project coordinator-demo \
  --capabilities runtime.claude,filesystem.read \
  --pool default \
  --enrollment-key 'PASTE_ENROLLMENT_KEY' \
  --work-repository /absolute/path/to/platform \
  --claude-analysis-model claude-fable-5 \
  --claude-analysis-effort medium \
  --claude-analysis-max-budget-usd 1.25 \
  --claude-review-model claude-opus-5 \
  --claude-review-effort max \
  --claude-review-max-budget-usd 2.50
```

The Codex model and reasoning effort apply to implementation and repair stages.
The example assigns Fable only to analysis and UI planning; the separate Opus
configuration applies only to review stages. Claude's installed CLI currently
names its highest effort `max`. Codex model/effort support varies by worker,
account, and CLI version: run `codex debug models` on that worker before choosing
a pair. For example, the current catalog advertises `ultra` for GPT-5.6 Sol and
Terra, `max` for Luna, and `xhigh` for GPT-5.5. Sagewai passes the selected native
model and effort through without storing a central model matrix.

These native runtime settings are worker-local: Sagewai never sends them to the
control plane, worker registration capabilities, Fleet task payloads, or Fleet
result payloads.

To advertise a HarnessRuntime worker, run a local OpenAI-compatible backend on
the worker and publish the `runtime.harness` capability. The implementer ladder
uses the `complex` tier, so a harness worker must configure that tier at worker
start:

```bash
sagewai fleet run --name harness-worker --project coordinator-demo \
  --capabilities runtime.harness,filesystem.write \
  --pool default \
  --enrollment-key 'PASTE_ENROLLMENT_KEY' \
  --work-repository /absolute/path/to/platform \
  --harness-tier complex=localai:mlx-community/Mistral-7B-Instruct-v0.3 \
  --harness-backend localai=http://127.0.0.1:8080/v1
```

`sagewai fleet run --harness-tier NAME=BACKEND:MODEL` splits on the first colon
after the backend name, so model names may contain colons. `--harness-backend
NAME=URL` appends `/v1` when missing, and every backend named by a configured
tier must have a matching `--harness-backend`. The control plane sends only the
tier name; the worker owns tiers, backend URLs, model names, and its own spend
metering. A worker may also configure `simple` or `medium` tiers for a future
router, but `runtime.harness` currently requires `complex` because that is the
tier dispatched for the implementer ladder.

The enrollment key authenticates registration without copying an Admin bearer
token to any worker. Open <http://localhost:3008/fleet>, verify the registered
workers are approved and online, and open each worker detail to check its
advertised capability. The detail page shows the organization ID needed below.

On the laptop, build the verifier as described in section 1, copy the printed
export, then select Fleet explicitly when starting the Work:

```bash
sagewai work --project coordinator-demo \
  --execution fleet --fleet-org YOUR_ORG_ID \
  start 'In test-apps/browser-game, make the requested bounded change and add deterministic tests.'
```

The control plane dispatches a credential-free workspace snapshot to a compatible
worker. If a worker disappears, the durable Work remains and Fleet lease recovery
can reassign the unfinished stage to another compatible, same-project worker.

The Admin UI currently manages worker enrollment, approval, status, and Work
observation. Start, resume, and named gate approval remain explicit laptop CLI
commands so the backend does not launch an unbounded coordinator process merely
because a browser request was made.

A Fleet resume must repeat the same route and organization selection:

```bash
sagewai work --project coordinator-demo \
  --execution fleet --fleet-org YOUR_ORG_ID \
  resume WORK_ID
```

Sagewai rejects a different route before it runs repository or model work.

## Activity and telemetry

Codex, Claude, HarnessRuntime, the verifier, and Fleet progress batches emit
`OperatorActivity` for model messages, reasoning, tool calls, tool results,
commands, file changes, usage, errors, and parser raw lines. `WorkActivityStore`
persists activity in `work_activity` by `project_scope_key`, `work_id`, `run_id`,
and sequence. SQLite deployments now require SQLite 3.35 or newer because the
store uses multi-row `INSERT ... ON CONFLICT DO NOTHING RETURNING`; PostgreSQL
is unaffected.

Activity rows are capped at 5,000 per run. Rows beyond the cap collapse into a
single `truncated` marker at sequence 5,000. No time-based retention job runs
for `work_activity` yet; rows persist until an operator prunes them. Native CLI
and harness runtimes also write the full
NDJSON activity stream as an artifact referenced from the `OperatorResult`;
that local archive keeps up to 4 MiB and ends with a `truncated` marker when
cut. Fleet result envelopes carry `activity_log` up to
`FLEET_ACTIVITY_LOG_MAX_BYTES` (2 MiB). The admin request cap
`SAGEWAI_MAX_REQUEST_BYTES` defaults to 10 MiB, leaving room for the bounded
Fleet envelope.

Local runtimes redact only credential values handed to the runtime through
scoped credentials. Fleet runs redact nothing because worker-local CLI
credentials are unknown to the platform and Fleet capabilities carry no
credential refs. Operators must keep secrets out of worker CLI output.

When a Work record has `profile_context["task_id"]`, activity ingestion also
mirrors entries into `task_feed`. `GET /api/v1/tasks/{id}/events` streams that
feed as Server-Sent Events. The stream replays stored entries after
`Last-Event-ID` and sends a heartbeat every 15 seconds, configurable with
`TASK_SSE_HEARTBEAT`.

`GET /api/v1/tasks/{id}/telemetry` returns
`sagewai.work.tasks.telemetry.derive_task_telemetry`, a pure projection over
Task events, Work events, and the spend ledger. It stores no new state. The
response includes `works[*].stage_attempts`, `works[*].verification_runs`,
`works[*].stage_timeline`, `works[*].attention_history`, `cycles[*]` with
`usd_actual`, `usd_reserved`, `usd_unknown`, `limits`,
`worst_case_next_attempt`, `free_attempts`, `paid_attempts`, `by_device`, and
`burn_series`, `scheduled` with `cycles`, `success_rate`,
`consecutive_failures`, `last_success_at`, and `overdue`, plus
`project.escalation_rate_per_role`. Decimal fields are JSON strings.
`worst_case_next_attempt` is `null` when the next implementer is Codex or no
selection exists yet; harness attempts are free with cost 0, and Fleet devices
appear as `fleet:<org>`. Task API callers should send project scope with
`X-Project-ID`; missing scope returns 400, while an unknown Task or a Task
outside the scope returns 404.

Fleet workers post live activity for claimed `work.operator` tasks to
`POST /api/v1/fleet/progress`. The endpoint accepts batches of at most 50
entries and 640 KiB, in sequence order per run, and feeds the same activity
store and Task feed path as final result ingestion.

Harness tiers live in project `task_defaults` as `simple`, `medium`, and
`complex` backend/model pairs. `sagewai work --project coordinator-demo
--prefer-free-implementation start ...` is off by default. On the local route it
reads `harness_tiers` from the project's task defaults, discovers local
OpenAI-compatible backends through `sagewai.harness.discovery`, and inserts the
configured `complex` harness tier before Codex on the implementer ladder. An
OpenAI-compatible server on port 8080, including LocalAI or `mlx_lm.server`, is
reported as `localai`; tiers reference that backend name. On the Fleet route,
`--prefer-free-implementation` dispatches `runtime.harness` with tier `complex`
before Codex on the implementer ladder only. On both routes, current harness
runtime grants are limited: `filesystem` and `browser` grants are served today,
while `cli:` grants need a sandbox backend and `mcp:` grants need the MCP
connection resolver (`sagewai.work.mcp_connection_resolver`); neither is wired
by the CLIs yet, so such grants fail the attempt.

## 7. Operate it as your middleman

A practical rollout is:

1. Start with read-only or repository-local changes that have deterministic tests.
2. Turn recurring requests into issue templates with explicit scope, acceptance,
   risk, and permissions.
3. Use one labeled GitHub intake path after the direct issue flow is reliable.
4. Check `pending` or `/work` rather than model sessions; approve only named gates.
5. Add a real external action only when a WorkContract names the adapter, target,
   blast radius, authority, observability, and rollback evidence.
6. Keep high-impact or irreversible actions behind explicit human approval.

This makes Sagewai the coordinator without making it an unbounded agent: models
are disposable workers, deterministic evidence decides progress, and loss of
control stops new side effects.

## Troubleshooting

- **Verification image rejected:** use an immutable digest, not a mutable tag, and
  confirm the image contains `just`, `uv`, Python 3, Node.js 20 or newer, and the
  repository's locked test environment.
- **Work needs attention:** run `pending`, act on the exact reported ID, then
  `resume`; do not restart the Work under a new ID.
- **No compatible Fleet worker:** verify project scope and advertised
  `runtime.codex`, `runtime.claude`, or `runtime.harness` capability. Do not
  send model credentials or harness backend configuration to the control plane.
- **Control degraded:** restore the failed authority, observability, or
  reversibility precondition. A successful HTTP status with stale observations is
  still degraded.
- **GitHub is unavailable:** retain the Work ID and resume after service recovers.
  Completed local implementation should not be repeated.
