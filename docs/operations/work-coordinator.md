# Using Sagewai as the work coordinator

Sagewai coordinates bounded software work between you, Codex, Claude, and a
deterministic verifier. You state an outcome and its limits. Sagewai persists
the contract and evidence, assigns write stages to Codex, assigns independent
read-only analysis and review to Claude, runs the repository's locked
verification contract, and stops when human attention or control evidence is
required.

The durable Work record is authoritative. Neither model's chat history is.

## What Sagewai automates today

The shipped software profile can coordinate a local request or GitHub issue
through analysis, implementation, verification, independent review, bounded
repair, pull request and merge gates, and a verified result. It can resume after
a process or worker disappears without repeating completed stages.

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

Verification is networkless and runs in a disposable container. Point Sagewai
at an immutable verifier image that contains the locked repository toolchain:

```bash
export SAGEWAI_WORK_VERIFICATION_IMAGE='registry.example/verifier@sha256:<digest>'
```

The image must be digest-pinned and contain `just`, `uv`, Python 3, Node.js 20 or
newer, and the repository's locked test environment. It must be able to run
`just smoke`; do not place model credentials or unrelated host secrets in it.

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

Run the stack and complete the first-time setup wizard:

```bash
just stack-up
```

Open <http://localhost:3008/setup> on a fresh installation, then
<http://localhost:3008/work>. The Work Control Console reads the same backend
state as `status` and `pending`; it does not maintain a browser-owned lifecycle.
Use it to inspect active Work, events, approvals, blocked questions, degraded
control, Evidence Board references, workers, and delivery observations.

## 6. Put Codex and Claude on separate workers

Connect each trusted checkout to the authenticated backend. Each worker advertises
only the capability it can provide and keeps its native CLI authentication local:

```bash
export SAGEWAI_ADMIN_URL=http://localhost:8000
export SAGEWAI_ADMIN_TOKEN='<tenant API token>'
```

Then start the project-scoped workers:

```bash
sagewai fleet run --name codex-worker --project coordinator-demo \
  --capabilities runtime.codex,filesystem.write \
  --work-repository /path/to/platform

sagewai fleet run --name claude-worker --project coordinator-demo \
  --capabilities runtime.claude,filesystem.read \
  --work-repository /path/to/platform
```

Workers without an enrollment key appear as pending in the Fleet console. Approve
each worker there before starting Work, or use a scoped enrollment key to
auto-approve it.

Select Fleet explicitly when starting the Work:

```bash
sagewai work --project coordinator-demo \
  --execution fleet --fleet-org YOUR_ORG_ID \
  start 'In test-apps/browser-game, make the requested bounded change and add deterministic tests.'
```

The control plane dispatches a credential-free workspace snapshot to a compatible
worker. If a worker disappears, the durable Work remains and Fleet lease recovery
can reassign the unfinished stage to another compatible, same-project worker.

A Fleet resume must repeat the same route and organization selection:

```bash
sagewai work --project coordinator-demo \
  --execution fleet --fleet-org YOUR_ORG_ID \
  resume WORK_ID
```

Sagewai rejects a different route before it runs repository or model work.

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
  `runtime.codex` or `runtime.claude` capability. Do not send model credentials to
  the control plane.
- **Control degraded:** restore the failed authority, observability, or
  reversibility precondition. A successful HTTP status with stale observations is
  still degraded.
- **GitHub is unavailable:** retain the Work ID and resume after service recovers.
  Completed local implementation should not be repeated.
