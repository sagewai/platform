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

## 5. Drive the coordinator from the console

For a first local or two-device Work test, run the backend, Admin UI, and Work
CLI on the same coordinator machine with the same `SAGEWAI_HOME`. This keeps
their SQLite Work and Fleet state shared:

```bash
export SAGEWAI_HOME="$PWD/.sagewai-dev"
just dev-all
```

Open <http://localhost:3008/setup> on a fresh installation, then use these
coordinator pages:

- **`/board`** — the board's five columns: `inbox`, `needs_you`,
  `planned`, `in_progress`, and `done`. The page has `Focus`, `Today`, and
  `All` views, title search over the pages already loaded, and kind and status
  filters that go to the API. The composer accepts a written brief or a dropped
  Markdown file; press `Preview` to see the template, kind, schedule, questions
  intake would ask, and the plain-language summary before creating the Task.
  Target and execution come from the project's `task_defaults`.
- **`/tasks`** — the same Tasks across every project the caller can see (an
  owner or admin sees every project of the organization; other members their
  memberships), up to 20 Tasks per project with no paging, and a needs-you
  count per project. The fan-out is server-side.
- **`/tasks/{id}`** — one Task with six tabs: Thread shows the brief, each
  open question with its answer control and deadline line, with `Use default`
  only when the question is defaultable, the coordinator messages, gates with
  `Allow` and `Deny`, outputs, and a discussion composer; Plan shows the
  accepted steps with their acceptance criteria, scope and dependencies, plus
  the acceptance matrix the assessor checks; Actions shows every side effect
  with its reversibility, rollback recipe and post-check, plus the button that
  requests the recorded rollback; Activity is paged operator activity with
  source, Work and run filters and a bounded download of the loaded rows;
  Telemetry shows spend per cycle, stage attempts per Work and schedule health
  for scheduled Tasks; Settings shows the definition read-only, an editable
  budget fenced on the record revision, and the project's triggers.
- **`/decisions`** — Task attention and Work attention merged, soonest due
  first. Both are settled in place: a gate goes to the route its `decided_by`
  field names (other Work gate classes show the `sagewai work approve` hint
  instead of buttons), and a clarification is answered at the
  `attention_version` the item carries. The inbox never offers `Use default`:
  it lists only the questions that have no default, so a defaultable one is
  answered — or left to its default — on the Task's Thread tab.
- **`/work`** — the project's active Work with its pending attention: active
  Work, events, approvals, blocked questions, degraded control, Evidence Board
  references, workers, and delivery observations, read from the same backend
  state as `status` and `pending`. It is the `Active Work` item in the same
  `Coordinator` nav group; `/decisions` links a Work item that carries no gate
  to its `/work/{work_id}` page; the Task pages name a Work id but do not link
  to it.

The header's `Pause`, `Resume`, and `Cancel` buttons call the same service
methods as `sagewai task pause|resume|cancel`. A `merge:` gate belongs to the
Work, so the console posts it to
`POST /api/v1/work/{work_id}/gates/{gate_id}` exactly as `sagewai work approve`
does.

The console does not yet have snooze or archive, server-side search, or the
plan's `Advanced` view. The board search box filters only the pages already
loaded. The Plan checklist and matrix are there; the dependency graph and raw
events are not.

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

The Task page's Activity and Telemetry tabs are the console views of the same
activity rows, Task feed entries, Work events, and spend-ledger projection.

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

Fleet workers and the control plane must run the same SDK version: the result
envelope and the progress path changed together, and neither side accepts the
other's older shape.

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

## Running the coordinator

A Task is a brief plus a target, a budget, and an authority. The coordinator plans it, opens
one GitHub issue per step, drives each step's Work to a merge, assesses the cycle, and
schedules the next one. It runs inside the backend and, for headless use, from the CLI.
Run the backend, CLI, and any Fleet workers on the same SDK version during coordinator
rollouts; the Task runner, Work lifecycle, activity feed, and Fleet envelopes share the
same event and result shapes.

Create a Task and drive it once:

```bash
sagewai task --project my-project create "Add a retry queue to the payments service"
sagewai task --project my-project tick
```

`create` prints the Task id. `tick` claims up to `SAGEWAI_COORDINATOR_MAX_TASKS` (default 2)
Tasks that are active or due, drives each under a 90-second lease with a 30-second
heartbeat, and prints how many it drove. The backend runs the same tick every
`SAGEWAI_COORDINATOR_INTERVAL_SECONDS` (default 5) and needs no extra process.

What the coordinator needs before the first tick:

- **Project defaults** with a software target: repository path, owner, repo, default branch,
  the digest-pinned verification image, and the locked verification commands. Set them
  through the console or `TaskStore.put_defaults`.
- **Harness tiers, if you set `prefer_free_implementation`.** The routing default is off. When
  it is on, the coordinator tries the local harness `complex` tier before Codex, and the
  project defaults must declare that tier — an empty `harness_tiers` raises
  `configure harness tiers in task defaults` at the first step.
- **A GitHub token** in `GITHUB_TOKEN` with permission to read the repository and to create
  issues, branches, pull requests, and comments, and to merge. It is read per call from the
  backend or CLI process environment through the injected credential callable and is never
  stored, logged, or passed to a subprocess.
- **A trusted checkout** at the target's repository path, with `origin` pointing at the
  GitHub repository. The coordinator fetches `origin/<default branch>` before every step and
  pins that head as the step's base. The fetch runs with a default-deny environment that
  carries `HOME` but not `SSH_AUTH_SOCK` or any `GIT_*` variable, so an **HTTPS remote with a
  credential helper works and an SSH remote that needs an agent does not**. Use an HTTPS
  origin for coordinator-driven repositories.

What it does per step: takes the repository lease for `project:owner/repo:branch` so only one
Task publishes to a branch at a time, creates an issue labelled `sagewai-task:<task id>`,
starts the issue's Work, and releases the lease when the step's outcome is recorded. If the
Task blocks while that step is open, the lease remains held until the step records an
outcome. The step issue carries the plan step's goal, acceptance criteria, and allowed scope
as prose; the Work contract fences the repository only. If the default branch moves under a step, the
step's Work is superseded and rerun on the new head; a merge-phase move is confirmed against
the pull request before anything is superseded.

Budget: each attempt reserves its tier's worst case before the call and settles the actual
cost after it. Crossing any limit — works, attempts, re-plans, seconds, or dollars — moves
the Task to `BUDGET_EXHAUSTED` and asks you. Codex attempts are counted, never priced.
Telemetry Decimal fields, including spend, are JSON strings.

Schedules and health: a scheduled Task fires at its cron in its own timezone, once per fire
even when the backend was down. Three consecutive failed cycles pause the schedule; a single
failed cycle is retried, counted against the budget; a cost or duration spike, or a success
rate below 80 percent over the last five cycles, raises an alert with a cooldown of one window. An alert holds the Task in `Needs you` until its next fire.
Nothing creates a monitoring Task on its own.

Questions and gates: an unanswered question that carries a default is defaulted once its
deadline passes and, from `Clarifying`, the Task returns to planning; a question that cannot
be defaulted stays
open and keeps the Task in `Needs you`. The plan and re-plan gates are decided on the Task; a
merge gate belongs to the Work, so approve it with `sagewai work approve` or
`POST /api/v1/work/{work_id}/gates/{gate_id}`.

A plan may arrive with a defaultable question attached: the coordinator proposes the plan and
records the question with its deadline, so the assumption is visible and correctable while the
cycle runs, and it is defaulted on that deadline like any other. Answering one after the plan is
accepted records the answer and nothing else — the planner reads it at the next plan version, so
use the re-plan gate when the answer should change the current plan. A question that cannot be
defaulted still stops the plan and asks first.

Triggers: an approved `github_label` trigger turns each newly labelled issue into one Task of
origin `trigger`, bounded by the trigger's authority, which a Task may only tighten. **A
non-human origin never merges automatically**: plan, merge, and deliver are forced to
`require` for every origin that is not a human, whatever the trigger says, and such a Task
never prefers the free implementation.

### The sagewai task command family

Every `sagewai task` command requires `--project <slug>`. `global` is refused because Tasks
have no organization-global scope.

Reading commands cover the project-local views the console uses: `list` accepts `--status`,
`--kind`, `--origin`, and `--column` filters plus `--limit` (a filtered page, oldest first;
there is no cursor); `board` groups the most recently touched Tasks into the five board
columns; `status` prints one Task's projection; `thread` prints the brief, messages,
questions, gates, plans, and outputs; `decisions` lists open human attention items; and
`templates` prints the intake templates. `create` and `intake` also accept `--file PATH` to
read the brief from disk instead of an inline argument.

Answering and deciding stay on the same service path as the API. `say TASK_ID TEXT` appends a
human message. `answer TASK_ID QUESTION_ID ANSWER [--attention-version N]` answers a
clarification, and `--use-default` applies the question's declared default instead of
passing text. The attention version fences the exact question text: if the coordinator asks a
new version of the question, an answer written against the old version is refused. `approve
TASK_ID GATE_ID [--deny --note ...]` decides Task gates. A `merge:` gate belongs to the Work,
so decide it with `sagewai work approve` or `POST /api/v1/work/{work_id}/gates/{gate_id}`.

Holding and stopping are explicit. `pause TASK_ID` holds the Task; `resume TASK_ID` returns it
to the status it was paused from; `cancel TASK_ID --note "..."` stops future coordinator
drives and records the optional note on the thread; a step Work a worker already claimed runs
to completion and a presented attention item is not retracted.

Previewing is deterministic. `intake BRIEF` shows the template, schedule band,
cron, and questions that `create` would use, without writing. `triggers list`, `triggers add`,
and `triggers remove` manage approved intake triggers. A trigger is admin-approved, and a
Task created by a trigger can never merge automatically.

The API adds authority-bearing writes over the CLI. Project members may create Tasks, post
messages, answer questions, decide `plan:` and `replan:` gates, pause, resume, and cancel.
Project admins also decide `deliver:` and `rollback:` gates, edit defaults and triggers, raise a budget, and request a rollback.
Organization admins can do all of those across the organization. `GET /api/v1/tasks/portfolio`
spans the projects the caller belongs to; that project set comes from memberships, never from
the request.

### When a Work is superseded

A superseded Work is not resumable or approvable. `sagewai work resume` and
`sagewai work approve` fail, naming the replacement Work id when the stream records one, so
continue from the Work that replaced it.

### Reports on a schedule

A scheduled report Task uses a `ReportTarget` instead of a software target. It needs source
grants, each with scoped `allowed_hosts`, plus `required_sections`, `max_bytes`, and at least
one sink. The console sink is added whenever no console sink is declared, at the next free
sink version; a GitHub issue sink also needs the issue URL it will comment on.

Report verification is deterministic and containerless. The compose stage snapshots every
source with its URL, fetch timestamp, and content hash, then verification checks size,
required sections, citations, allowed hosts, and forbidden secret patterns. The report and
its source snapshots are redacted before they are stored.

### Who approves what

The default action policy is by reversibility:

| Reversibility | Default decision |
| --- | --- |
| `pure` | Never gates. |
| `snapshot_reversible` | Never gates. |
| `compensatable` | Runs automatically only when the action already declares a rollback recipe and post-check the coordinator can execute. This covers merges with `revert_pull_request` and GitHub issue comments with `delete_comment`. |
| `irreversible` | Always asks a project admin. |

A project may tighten authority to `require`. A non-human origin is tighter still: plan,
merge, and deliver are forced to `require` whatever the approved trigger says.
The `/decisions` page is the inbox for those Task and Work approvals.

### When something has to be undone

Every coordinator side effect records four durable facts in order:
`ACTION_INTENT_RECORDED`, the `task_commands` receipt before the call,
`ACTION_RESULT_RECORDED`, and `OBSERVATION_RECORDED`. Rollback is another coordinator action
with the same record sequence; no model chooses or runs a rollback recipe.

A failed post-check that can be undone opens a Task gate named `rollback:<work_id>`; decide it
with `sagewai task --project P approve TASK_ID rollback:<work_id>` or
`POST /api/v1/tasks/{task_id}/gates/{gate_id}` (project admin). A rollback runs at most once. If the receipt exists
but the result is unknown, the coordinator blocks and asks a human instead of retrying the
side effect.

### Decision channels

Projects choose the ordered channel list in `task_defaults.decision_channels`. `console` is
always available; `github_issue` uses the Task tracking issue or report sink issue. Slack and
Google Chat use incoming-webhook URLs from the notification channel store, where the webhook
URL is encrypted at rest.

The coordinator reads the rows the admin channel routes write in both deployment modes:
tenant-key-encrypted `admin_resources` rows in multi-tenant mode, decrypted under the row's
own project key, and the state file's `notification_channels` in single-org mode. A channel
configured in the console works from the backend and from `sagewai task tick`; no out-of-band
row is needed. A row that cannot be read is skipped with a warning naming the channel, never
its URL, and the item falls back to the console instead of stalling the tick. An
organization-shared channel is inherited by every project and, because `admin_resources`
carries no organization column, such a row is deployment-wide in the current
one-org-per-deployment model.

`Needs you` due times are derived from urgency:

| Urgency | `due_at` |
| --- | --- |
| `now` | Immediately. |
| `today` | 24 hours from presentation. |
| `this_week` | Seven days from presentation. |

An open clarification deadline overrides those values when it is sooner. `now` notifies every
configured channel at once; `today` and `this_week` start with the first channel and escalate
to the next one after half the remaining time. A `github_issue` channel is presented to
regardless of urgency and position, because the tracking issue is the durable log. A channel
failure deletes that channel's present-once receipt; when every selected channel fails, the
item falls through to the next channel in the same tick, ultimately the console, so a `today`
item still reaches someone immediately. A project naming an unresolvable webhook channel falls
back to the console; `github_issue` requires the tracking channel supplied by the coordinator
wiring, and the log names the channel, never a URL.

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
