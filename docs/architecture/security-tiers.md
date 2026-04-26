# Sagewai security tiers

**Status:** authoritative
**Last revised:** 2026-04-25
**Companion docs:** [runtime-topology.md](runtime-topology.md), [execution-modes.md](execution-modes.md), [execution-backends.md](execution-backends.md)

This document fixes the credential layering: where keys live, who reads them, who never sees them.

---

## The two tiers

Sagewai has two distinct LLM-key surfaces with different lifetimes, sources, and trust boundaries.

### Tier 1 — Orchestration keys

**Purpose:** the Sagewai Agent's own LLM calls, used to plan + dispatch.
**Lifetime:** long-lived; set when a worker process starts.
**Source:** worker process environment (operator's infra config — env file, k8s Secret, vault sidecar — operator picks).
**Lives in:** worker process memory.
**Read by:** Sagewai Agent process, on the worker host.
**Used for:** "given this user goal, which CLI agent should I dispatch and with what prompt?" — the planning brain. Often a small / cheap / local model is appropriate (Ollama Mistral, Claude Haiku, GPT-4o-mini).
**Sealed coverage:** none. Sealed manages Tier-2; Tier-1 is the operator's plain infra concern.

Examples:

```
ORCHESTRATION_OPENAI_KEY=sk-...
ORCHESTRATION_ANTHROPIC_KEY=sk-ant-...
ORCHESTRATION_OLLAMA_URL=http://localhost:11434
```

### Tier 2 — User-task keys

**Purpose:** keys that CLI agents (Claude Code, Codex, Gemini, custom) and tool functions inside the sandbox use to do the actual user task.
**Lifetime:** short-lived; injected per sandbox instance, scrubbed on release (Sealed-iii.A `cleanup_run`).
**Source:** Sealed Identity (cascade of system → workflow → user-level profile refs + overrides; resolved at enqueue, re-resolved at sandbox-start to catch drift).
**Lives in:** sandbox container `os.environ` only.
**Read by:** tool runner subprocesses + CLI agents inside the sandbox.
**Used for:** the actual customer-facing work — "Claude Code calls Anthropic to write Python", "git pushes to a customer repo", "AWS S3 uploads an artifact bundle".
**Sealed coverage:** full. Sealed-i (foundation), Sealed-ii (external backends), Sealed-iii.A (revocation), Sealed-iii.B (redaction), Sealed-iii.D (per-key ACL), Sealed-iv (HITL/JIT) all govern this tier.

Examples (from a single Sealed Identity profile):

```
ANTHROPIC_API_KEY=sk-ant-…   ← Claude Code uses this
OPENAI_API_KEY=sk-…          ← Codex uses this
GEMINI_API_KEY=…             ← Gemini CLI uses this
GITHUB_TOKEN=ghp_…           ← git push to artifact repo uses this
AWS_ACCESS_KEY_ID=…          ← aws s3 sync uses this
AWS_SECRET_ACCESS_KEY=…
DEBUG=1                      ← behavior knob, not a secret
MAX_TOKENS=8000              ← behavior knob
```

---

## Visual: who sees what

```
┌──────────────────────────────────────────────────────────────────────┐
│  CONTROL PLANE   (admin server)                                       │
│  Sees: Tier-1 NEVER. Tier-2 NEVER (just key NAMES via Sealed audit).  │
│  Postgres rows know: profile_ref + effective_*_keys (NAMES).          │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  WORKER HOST                                                          │
│  Sees: Tier-1 (its own process env). Tier-2 NEVER (only NAMES).       │
│  Sagewai Agent reads Tier-1 to make ITS LLM calls.                    │
│  Sagewai Agent NEVER decrypts Tier-2 — that work happens at the       │
│  Sealed Identity ↔ sandbox boundary, never traversing the host.       │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  SANDBOX  (Mode 1+)                                                   │
│  Sees: Tier-2 only, in os.environ.                                    │
│  Tool runner + CLI agents read os.environ for their LLM keys + creds. │
│  No Tier-1 access (sandbox env is wiped of host vars at start).       │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  LLM INFERENCE POINT  (external)                                      │
│  Sees: prompts + tool schemas only.                                   │
│  Never sees: secret VALUES (unless a poorly-written agent embeds      │
│  them in a prompt — that's Sealed-iii.B's redaction concern).         │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Operator vs end customer

Sagewai has two human roles in the credential model:

| Role | Owns | Configures | Audited via |
|---|---|---|---|
| **Operator** | the worker fleet, the control plane, Tier-1 keys | worker env, autopilot config, Sealed system-level config, image catalog | their own infra audit (k8s audit logs, IAM trails, etc.) — outside Sagewai |
| **End customer / project owner** | per-project Sealed profiles, artifact destinations | profile ref on workflows, identity content via admin UI / CLI / external backend | sealed_audit_events table — every reveal, every injection, every revocation |

**Important:** operators MAY also be customers (single-tenant Sagewai installs). The role split is logical, not organisational. Even a one-person ops + dev install benefits from the tier separation: Tier-1 is "your personal Ollama URL" and Tier-2 is "the API keys for the project you happen to be building right now."

---

## What Sagewai promises (and does not)

### Sagewai promises

1. **Tier-2 plaintext never crosses the worker host process boundary.** The `SecretProvider.env_for(...)` returns env that is set on container start by the sandbox backend's native env-injection primitive. It is never logged, never written to postgres in plaintext (even via Sealed-i's encrypted profile file, only the encrypted form is on disk).

2. **Every Tier-2 read is audited.** `secret.decrypted`, `profile.injected`, `profile.cascade.resolved` audit events name every key that was decrypted, on what run, by what actor — without naming the value.

3. **Mid-run revocation works.** Sealed-iii.A: an operator can revoke a `(profile_id, secret_key)` pair; future enqueues fail-closed; in-flight runs that already injected the value get aborted (hard-revoke) or silently expire on next sandbox-start (soft-revoke).

4. **Cascade rotation is observable.** When a profile is rotated between enqueue and sandbox-start, `profile.drift_at_injection` is emitted with the diff (added keys, removed keys).

5. **Pool reuse is safe.** Sealed-iii.A `cleanup_run` scrubs Tier-2 env on release. If cleanup fails, the sandbox is discarded, not pooled.

6. **Fail-closed on registry unreachability.** If Postgres is unreachable when Sealed needs to consult the revocation registry, neither enqueue nor sandbox-start proceed — they raise `RevocationCheckUnavailableError`.

### Sagewai does not promise

1. **Tier-1 protection.** Tier-1 keys are the operator's responsibility. Sagewai does not encrypt them, does not rotate them, does not manage their lifecycle. Use your existing infra (k8s Secrets, AWS Secrets Manager, dotenv, whatever).

2. **Backend-escape immunity.** A vulnerability in the chosen sandbox backend (Docker daemon, k8s kubelet, Lambda runtime) that lets sandbox code escape to the host is the backend vendor's problem. Sagewai uses defense-in-depth (NetworkPolicy, resource limits, image variants without unnecessary tooling) but cannot defeat backend escapes.

3. **LLM provider trust.** When a CLI agent calls Anthropic / OpenAI / etc., that provider receives the prompt content. If the prompt contains secrets (because a redaction rule was missing — Sealed-iii.B), the provider sees them. Sagewai cannot retroactively unsee.

4. **Out-of-band exfiltration.** A malicious CLI agent inside a sandbox with `NetworkPolicy.FULL` can call any URL it likes. The deployment policy ("which sandbox image variant has which CLI") is the operator's responsibility. Don't put untrusted CLIs in `FULL` networks.

---

## How Tier-2 actually flows

The end-to-end path of a single Tier-2 secret (e.g. `OPENAI_API_KEY` in profile `acme-prod`) at a Mode 3 step:

```
1. Operator creates profile via admin UI / CLI:
       admin UI → POST /api/v1/admin/sealed/profiles
       Backend: BuiltinAdminStoreBackend (or Sealed-ii: Vault, 1Password, …)
       Encrypted at rest with master key (Fernet wrapping)
       Sealed audit: profile.created
       ↓
2. Workflow definition references the profile:
       wf.enqueue(security_profile_ref="acme-prod")
       OR set at the workflow level:
       admin-state.workflows[wf_name].security_profile_ref = "acme-prod"
       ↓
3. Enqueue resolves the cascade (system + workflow + user):
       resolve_security_profile(levels=[system, workflow, user],
                                revocation_registry=…)
       Returns EffectiveProfile{env, secret_keys, cascade_origins}
       Sealed audit: profile.cascade.resolved
       ↓
4. workflow_runs row persisted with key NAMES only:
       effective_env_keys = ['DEBUG', 'OPENAI_API_KEY']
       effective_secret_keys = ['OPENAI_API_KEY']
       security_profile_ref = 'acme-prod'
       NEVER plaintext values.
       ↓
5. Worker claims run, dispatches by mode (Mode 3 in this example):
       Sagewai Agent calls SealedSecretProvider.env_for(...)
       which:
       a) re-resolves cascade (catches rotation drift)
       b) checks revocation registry — fails closed if unreachable
       c) returns env dict to pool
       Sealed audit: profile.injected, secret.decrypted (per key)
       ↓
6. Sandbox backend sets env on container:
       Docker:  --env OPENAI_API_KEY=sk-…
       K8s:     pod.spec.env or projected secret
       Lambda:  function configuration env
       Plaintext value lives ONLY here, only for the run's lifetime.
       ↓
7. CLI agent inside sandbox reads:
       claude-code CLI → reads os.environ["ANTHROPIC_API_KEY"]
       openai-codex   → reads os.environ["OPENAI_API_KEY"]
       Calls the LLM inference point.
       ↓
8. Run completes:
       Pool calls SealedSecretProvider.cleanup_run
       Tool runner unsets env vars in the container
       (or container is discarded entirely if cleanup fails)
       Sealed audit: pool.sandbox.reset
       ↓
9. workflow_runs.status = 'completed', sandbox returned to pool
   (if pool reset succeeded) or destroyed (if it failed).
```

---

## Anti-patterns

1. **Putting Tier-2 keys in worker env.** Worker env is Tier-1 only. If `OPENAI_API_KEY` is needed by a customer's tool, it goes in their Sealed profile, never in the worker process.

2. **Reading Tier-1 keys from inside the sandbox.** The sandbox should not need orchestration keys. If a CLI agent inside the sandbox needs to make orchestration-style decisions, that's a design smell — orchestration belongs to the Sagewai Agent on host.

3. **Logging plaintext secret values.** `logger.info(f"calling api with {api_key}")` is forbidden anywhere in the codebase. Audit events log key NAMES; redaction (Sealed-iii.B) scrubs values from prompts/outputs.

4. **Persisting plaintext.** Postgres `workflow_runs.effective_secret_keys` is `text[]` of names. The actual values exist only in sandbox memory.

5. **Trusting the LLM's "I'll keep it secret".** Don't ask a model to "be careful with this API key" and then put the key in the prompt. Either redact (Sealed-iii.B) or don't include.

6. **Using one profile for both Sagewai and customer keys.** Tier-1 is the operator's own infra config. Tier-2 is the customer's identity. Mixing these defeats the role separation.

---

## Trust assumption summary

| Surface | Trusted with Tier-1? | Trusted with Tier-2? |
|---|:---:|:---:|
| Operator's infra config (env files, k8s Secrets) | ✓ | n/a (Tier-1 only) |
| Worker process memory | ✓ | ✗ |
| Postgres `workflow_runs` columns | ✗ | ✗ (names only) |
| Postgres `sealed_revocations` | ✗ | ✗ (names only) |
| Postgres `sealed_audit_events.details` | ✗ | ✗ (names only; values forbidden) |
| `~/.sagewai/profiles.json` (Builtin Identity backend) | ✗ | ✓ (encrypted-at-rest, Fernet) |
| External Identity backend (Vault, 1Password, …) | ✗ | ✓ (per their security model) |
| Sandbox `os.environ` (in-memory, container-scoped) | ✗ | ✓ |
| Container filesystem (`/workspace`, `/tmp`, …) | ✗ | ✗ unless explicitly written by tool runner — usually not |
| LLM inference point (external) | ✗ | ✗ (prompts must be redacted) |
| Artifact destination (GitHub repo, S3, …) | ✗ | ✗ (CLI agent uses creds locally; never embeds in artifact content) |

The "✓ for Tier-2" rows are the trust boundary. Everything else must treat Tier-2 as forbidden plaintext.
