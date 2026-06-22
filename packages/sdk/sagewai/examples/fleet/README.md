# Run an agent on a Fleet worker

This walks the **real, end-to-end loop**: create a worker, approve it, put a job on
the fleet, and have the worker **execute an agent** and report the result.

```
sagewai fleet run --exec agent_task_handler   ─┐   (worker: registers, heartbeats,
                                                │    long-polls for tasks)
approve in the admin panel  ────────────────────┤
sagewai fleet enqueue --agent helper -m "…"  ──►│   gateway queues a task
                                                ▼
       worker claims it → runs UniversalAgent → reports the reply
```

> **Scope.** This is the operator path to dispatch **standalone agent tasks** to your
> fleet, and it runs on the shipped, hardened runtime: a **durable task queue** (tasks
> survive a gateway restart), **per-worker credentials**, token auth, project isolation,
> and the `--exec` env allowlist. If a worker dies mid-task, the task is **automatically
> requeued** (lease + reaper — see [Durability & recovery](#durability--recovery)).
> Wiring Autopilot *multi-step missions* to remote workers is a separate, deferred track
> (sagewai/atelier#59) and does not affect this path.

## Fastest path — `just` (single-user, local)

On your own machine these recipes run the whole loop with **auto-auth** (loopback
dev-trust — no manual token or login). In two terminals:

```bash
# terminal 1 — gateway: auto-creates an admin, serves with dev-trust
just fleet-demo-up

# terminal 2 — prove it end-to-end with NO LLM key, then run real agents
just fleet-selftest
just fleet-run-agent w1 gpt-4o-mini --env OPENAI_API_KEY=sk-...
just fleet-approve-all
just fleet-enqueue helper "Summarize Sagewai in one line." gpt-4o-mini
```

> Single-user / single-org / loopback only — don't point these at a shared gateway.
> `just --list | grep fleet` shows them all. The sections below are the explicit,
> step-by-step path (and what production looks like).

## Prerequisites

- The gateway (admin server) running, reachable at `SAGEWAI_ADMIN_URL`.
- A tenant **API token** (admin panel → API tokens) — authorizes registration and
  `enqueue`. The worker then gets its **own** per-worker secret at registration
  (captured automatically, stored under `$SAGEWAI_HOME/fleet/`).
- An **LLM provider key** for the agent (e.g. `OPENAI_API_KEY`).

```bash
export SAGEWAI_ADMIN_URL=http://localhost:8000
export SAGEWAI_ADMIN_TOKEN=<api-token>
```

## 1. Start a worker that runs agents

On the machine that will do the work:

```bash
sagewai fleet run --name worker-01 --models gpt-4o-mini --pool default \
  --max-concurrent 2 \
  --env OPENAI_API_KEY=sk-... \
  --exec 'python ./agent_task_handler.py'
```

- `--models` must include the model you'll enqueue (it's how tasks are matched).
- The `--exec` command runs **once per claimed task**: the worker pipes the task
  JSON to it on stdin, and [`agent_task_handler.py`](agent_task_handler.py) runs a
  `UniversalAgent` and prints the reply. The handler ships in this directory
  (`sagewai/examples/fleet/agent_task_handler.py`) — copy it next to where you launch
  the worker (so `./agent_task_handler.py` resolves) or use its absolute path.
- **Isolated env, explicit grants:** the daemon runs each task in an isolated
  environment that is **default-deny** — every ambient secret (`SAGEWAI_ADMIN_TOKEN`,
  DB URL, other API keys) is scrubbed, so untrusted task code can't reuse the
  daemon's credentials. You grant exactly what the task needs with `--env KEY=VALUE`
  (repeatable) or `--env-file PATH` — here, the agent's `OPENAI_API_KEY`. Nothing
  else leaks in.

The worker registers and starts heartbeating. It shows up as **pending** in the
admin panel.

## 2. Approve it

Admin panel → **Fleet → Fleet Workers** → your worker → **Approve**.
(Or skip this: pass `--enrollment-key <swk_…>` in step 1, after creating a key under
**Fleet → Enrollment Keys**, and it auto-approves.)

The worker is now **approved** and long-polling — idle until you give it work.

## 3. Enqueue a task

From anywhere with the token:

```bash
sagewai fleet enqueue --agent helper \
  --message "Summarize the five pillars of Sagewai in one sentence." \
  --model gpt-4o-mini
# → Enqueued task 7f3c… (pool=default, model=gpt-4o-mini)
```

The gateway stamps the task with your token's **project** (so only your own,
approved, same-project workers can claim it) and queues it.

## 4. Watch it run

Within a second or two the worker claims the task, runs the agent, and reports.
You'll see it in:

- the **worker's stdout/logs** (the claim → exec → report cycle), and
- the admin panel → the worker's **Activity** tab (claim/report events).

To run a single task and exit (handy for testing), start the worker with `--once`
instead of the long-running daemon.

## Container isolation (`--image`)

For stronger isolation, run **each task inside a fresh container** — the daemon does
the `docker run` for you, no wrapper script needed. Just point `--image` at an image
that contains the handler:

```bash
sagewai fleet run --name worker-01 --models gpt-4o-mini \
  --image my-task-image:latest \
  --exec 'python /app/agent_task_handler.py' \
  --env OPENAI_API_KEY=sk-... \
  --docker-arg --memory=512m --docker-arg --cpus=1 \
  --docker-arg --read-only --docker-arg --cap-drop=ALL
```

Per claimed task the worker runs:

```
docker run --rm -i <your --docker-arg flags> \
  -e OPENAI_API_KEY=… -e SAGEWAI_TASK_RUN_ID=… -e SAGEWAI_TASK_JOB_ID=… \
  -e SAGEWAI_TASK_MODEL=… -e SAGEWAI_TASK_POOL=… \
  my-task-image:latest sh -c 'python /app/agent_task_handler.py'
```

- The container starts **clean**: only your `--env` vars and the `SAGEWAI_TASK_*` IDs
  are passed in (via `-e`) — no host environment at all. Default-deny is automatic.
- The task JSON is piped to the container on **stdin**; its stdout is the report
  output, exit code the success/failure.
- `--docker-arg` passes any `docker run` flag through (resource caps, read-only fs,
  network policy, volume mounts). Add `--docker-arg --network=none` to cut network if
  the task doesn't need it.

## What the worker hands your task (either mode)

- **stdin:** the full task JSON (`run_id`, `model`, `pool`, `labels`, `payload`).
- **env:** `SAGEWAI_TASK_{RUN_ID,JOB_ID,MODEL,POOL}` + whatever you pass with
  `--env`/`--env-file`. **Host mode** also keeps a minimal system allowlist
  (`PATH`, `HOME`, locale, …); **container mode** passes nothing but those `-e` vars.
  Every other ambient variable is dropped in both.
- **result:** exit `0` → reported `completed` (stdout = output); non-zero / timeout →
  `failed` (stderr = error).

## Durability & recovery

The task path on this page is durable end-to-end:

- **Durable queue** — enqueued tasks are persisted (SQLite by default, Postgres in
  production), so they survive a gateway restart; a task is never lost between
  `enqueue` and `claim`. Claims are atomic across many workers
  (`SELECT … FOR UPDATE SKIP LOCKED` on Postgres / a guarded CAS on SQLite), so exactly
  one worker wins each task.
- **Dead-worker recovery** — each claim takes a **lease** that the worker's heartbeat
  keeps fresh. If a worker crashes or is killed mid-task, its lease expires and a
  background **reaper** requeues the task for another worker (or fails it after a few
  attempts, to bound a poison task). This is at-least-once execution, with no leader.
- **Per-worker credentials** — the worker authenticates `claim`/`report`/`heartbeat`
  with **its own secret**, captured at registration and stored under
  `$SAGEWAI_HOME/fleet/` — not the shared API token (which only authorizes
  registration and `enqueue`).

## Still simulated (a separate track)

The handler here runs an inline `UniversalAgent`. Richer agent-spec management and
wiring Autopilot *multi-step missions* to remote workers are deferred to
sagewai/atelier#59 — the **standalone task path on this page is fully real and shipped**.
