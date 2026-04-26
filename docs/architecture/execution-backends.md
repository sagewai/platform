# Sagewai execution backends

**Status:** authoritative
**Last revised:** 2026-04-25
**Companion docs:** [runtime-topology.md](runtime-topology.md), [security-tiers.md](security-tiers.md), [execution-modes.md](execution-modes.md)

This document fixes the pluggable backend abstraction. Two distinct backend types exist; do not conflate them:

1. **Sandbox backend** — where the sandbox runs (Docker / Kubernetes / Lambda / Null).
2. **Identity backend** (a.k.a. ProfileBackend) — where Sealed Identity values come from (Builtin file / Vault / 1Password / AWS SM / SOPS / Bitwarden).

The two are orthogonal: a deployment can run a Kubernetes sandbox backend with a Vault identity backend, or a Docker sandbox backend with a Builtin identity backend, etc.

---

## Sandbox backends

The sandbox backend determines *where* the sandbox container, pod, or function lives. It is selected per worker (each worker advertises its sandbox backend via fleet capability labels; the autopilot routes runs to capable workers).

### The Protocol

```python
# packages/sdk/sagewai/sandbox/backend.py

class SandboxBackend(Protocol):
    name: str

    async def start(
        self,
        *,
        project_id: str,
        run_id: str,
        image: str,
        image_digest: str,
        env: dict[str, str],
        network_policy: NetworkPolicy,
        resource_limits: ResourceLimits,
        workdir_mount: Path,
        lifetime: SandboxLifetime,
    ) -> SandboxHandle: ...

    async def probe_runner(
        self, handle: SandboxHandle
    ) -> bool: ...
```

A `SandboxHandle` is the interaction surface — `.exec(cmd, args)`, `.stop()`, `.network_policy`, etc.

### Implementations

| Backend | Used for | Modes supported | Cold-start | Pool support | Status |
|---|---|---|---|---|---|
| `NullBackend` | Mode 0 only — no actual sandbox | 0 | n/a | n/a | shipped |
| `DockerBackend` | local dev, single-VM ops, simple production | 0, 1, 2, 3, 3b | 2-8s | warm container pool (Plan 1.5) | shipped |
| `KubernetesBackend` (planned) | production at scale, multi-tenant clusters | 0, 1, 2, 3, 3b | 5-15s | Deployment with min-replicas | Plan SBX-K8S |
| `LambdaBackend` (planned) | event-driven, scale-to-zero, short tools | 0, 1, 2 only | ~ms (warm), ~1-3s (cold); provisioned concurrency for hot paths | provisioned concurrency | Plan SBX-LAMBDA |
| `FirecrackerBackend` (future) | ultra-isolated tenants on shared hosts | 0, 1, 2, 3, 3b | <1s | warm microVM pool | not planned |
| `gVisorBackend` (future) | enhanced isolation over Docker | 0, 1, 2, 3, 3b | similar to Docker | similar to Docker | not planned |

### Mode × Backend compatibility matrix

This is the critical table. Every workflow's `requires_sandbox_mode` must be supported by at least one available worker's backend.

| Mode | Null | Docker | Kubernetes | Lambda |
|---|:---:|:---:|:---:|:---:|
| **0 — Bare** | ✓ | ✓ | ✓ | ✓ |
| **1 — Sandboxed (no identity)** | ✗ | ✓ | ✓ | ✓ |
| **2 — Identity** | ✗ | ✓ | ✓ | ✓ |
| **3 — Full (CLI agent)** | ✗ | ✓ | ✓ | ✗ |
| **3b — Full + JIT callback** | ✗ | ✓ | ✓ | ✗ |

**Why Lambda doesn't support Mode 3:**

- 15-minute hard execution timeout — too short for typical CLI agent sessions (Claude Code can run 30+ minutes on complex prompts).
- No persistent filesystem (10 GB ephemeral, gone at function end) — multi-step CLI sessions can't accumulate work.
- No interactive shell, no long-lived RPC daemon — the bidirectional callback channel (Mode 3b) is awkward to maintain over Lambda's request-response model.
- Each invocation is a fresh container — the pool reset model is "the runtime always discards"; provisioned concurrency mitigates cold-start cost but doesn't change the model.

**Why Kubernetes supports all modes:**

- Pods can run for hours (matches Mode 3 CLI agent durations).
- Multi-container pod pattern (CLI agent + sidecar tool runner) is clean.
- Native security primitives map cleanly: `NetworkPolicy` resource → our `NetworkPolicy` enum.
- StatefulSet supports persistence-across-runs if a workflow benefits from it.
- Cluster autoscaler handles capacity for pool scaling.

### Per-backend primitive translations

Each backend translates the abstract sandbox primitives to its native API:

| Primitive | Docker | Kubernetes | Lambda |
|---|---|---|---|
| Sandbox lifecycle | container start / stop | pod create / delete (or Job) | function invoke |
| Image | `ghcr.io/sagewai/sandbox-base:VERSION` | same image, pulled to pod | container image deployed to Lambda or zip |
| Env injection | `--env K=V` on container start | `env:` field on pod spec or projected secret | function configuration env |
| Tool runner exec | `docker exec` | `kubectl exec` (or pod-init script that starts the daemon) | function payload (single-shot RPC) |
| `NetworkPolicy.NONE` | iptables drop / no network | NetworkPolicy `egress: []`, `ingress: []` | VPC isolated subnet, no NAT gateway |
| `NetworkPolicy.EGRESS_ONLY` | bridge network, no inbound | NetworkPolicy egress to allowed CIDRs | VPC with NAT gateway, no inbound |
| `NetworkPolicy.FULL` | host network or default bridge | default networking | normal Lambda networking |
| Pool reuse (Plan 1.5) | warm container pool, reset env between runs | warm pod pool via Deployment with min-replicas, reset env | provisioned concurrency (limited reuse model) |
| `cleanup_run` (Sealed-iii.A) | `docker exec` to unset env vars | `kubectl exec` to unset env vars | n/a (every invoke is fresh; nothing to scrub) |
| Resource limits | `--memory --cpus` | `resources.limits.memory/cpu` | function memory configuration (CPU is a function of memory) |
| Workdir mount | `--mount` bind to host path or named volume | `volumeMounts` + `volumes` (PVC, emptyDir, hostPath) | `/tmp` (ephemeral 10 GB max) |
| Image digest pinning | `image@sha256:...` | same | container image URI with digest |

### Heterogeneous fleets

Sagewai supports a fleet where different workers run different sandbox backends. Each worker advertises its backend via `Worker.advertised_labels`:

```python
{
    "sandbox.backend": "docker",       # or "kubernetes" or "lambda" or "null"
    "sandbox.image_variants": "base,claude-code,codex",
    "models_supported": "claude-3-5-sonnet,gpt-4",
    "pool": "production",
}
```

The fleet dispatcher matches a run's requirements against worker labels. A run that requires Mode 3 with the `claude-code` image variant won't route to a Lambda-backend worker; it will route to a Docker or k8s worker that advertises the variant.

This means a deployment can:

- Use Docker workers for dev/CI (low overhead, shared tooling).
- Use Kubernetes workers for production scale.
- Use Lambda workers for sporadic Mode 1/2 jobs that benefit from scale-to-zero economics.

All in the same fleet, with workflows automatically routed appropriately.

### Backend selection guidance

For most operators:

- **Single VM / dev laptop** → Docker.
- **Self-managed production** → Docker (small) or Kubernetes (scale).
- **Multi-tenant SaaS at scale** → Kubernetes. Per-namespace isolation, RBAC, NetworkPolicy.
- **Sporadic batch workloads with cost sensitivity** → Lambda for the Mode 1/2 portion + Docker/K8s for the Mode 3 portion.
- **Highest-isolation tenants** → wait for FirecrackerBackend or use a dedicated cluster per tenant on Kubernetes.

A single deployment can mix: most workers on Kubernetes, a few Lambda workers for cost-optimised small tasks.

---

## Identity backends (ProfileBackend)

The identity backend is *where Sealed Identity values come from*. It is selected at the platform level (Sagewai admin process picks; same backend for all workers in a deployment, but the cascade can reference profiles in different backends via URI scheme).

See the [Sealed-i design spec](../superpowers/specs/2026-04-25-sealed-i-profile-management-design.md) for the full Protocol.

### The Protocol

```python
# packages/sdk/sagewai/sealed/backend.py

class ProfileBackend(Protocol):
    name: str
    scheme: str  # the URI scheme for ProfileRef (e.g., "builtin", "vault")

    async def list_profiles(self) -> list[ProfileMetadata]: ...
    async def get_profile_metadata(self, profile_id: str) -> ProfileMetadata: ...
    async def get_profile(self, profile_id: str) -> Profile: ...
    async def save_profile(self, payload: ProfileWritePayload) -> Profile: ...
    async def delete_profile(self, profile_id: str) -> None: ...
    async def supports_master_key_rotation(self) -> bool: ...
    async def rotate_master_key(self, new_key: bytes) -> int: ...
```

A ProfileRef like `vault://kv/sagewai/acme-prod` parses to `(scheme="vault", path="kv/sagewai/acme-prod")` and dispatches to the registered Vault backend.

### Implementations

| Backend | Scheme | Storage | Master key needed | Status |
|---|---|---|---|---|
| `BuiltinAdminStoreBackend` | `builtin` (default if no scheme) | `~/.sagewai/profiles.json`, encrypted at rest with Fernet | yes (Sealed-i master-key resolution chain) | shipped |
| `VaultBackend` (planned) | `vault` | HashiCorp Vault KV v2 | n/a (Vault holds its own root key) | Sealed-ii |
| `OnePasswordBackend` (planned) | `onepassword` | 1Password Connect API | n/a | Sealed-ii |
| `AWSSecretsManagerBackend` (planned) | `aws-sm` | AWS Secrets Manager | n/a (KMS-backed) | Sealed-ii |
| `SOPSBackend` (planned) | `sops` | SOPS-encrypted YAML/JSON in git | yes (SOPS keys: PGP, age, KMS) | Sealed-ii |
| `BitwardenBackend` (planned) | `bitwarden` | Bitwarden CLI / Secrets Manager | n/a | Sealed-ii |

### Backend selection guidance

- **Local dev / single-machine** → `BuiltinAdminStoreBackend`. No external infra, master key stored in OS keychain or a 0600 file.
- **Existing Vault deployment** → `VaultBackend`. Sagewai becomes a Vault consumer, not a credential store. Operators get Vault's audit, rotation, lease management for free.
- **AWS-native shop** → `AWSSecretsManagerBackend`. KMS-backed, IAM-scoped, integrates with existing AWS audit.
- **GitOps-friendly** → `SOPSBackend`. Profiles are SOPS-encrypted YAML in a git repo; changes go through code review.
- **Per-end-user vault** → `OnePasswordBackend` or `BitwardenBackend`. Useful for SaaS where each customer has their own 1Password / Bitwarden vault.

### Cascade across multiple identity backends

A single Sealed cascade can mix backends — each `CascadeLevel.profile_ref` parses its own URI:

```python
levels = [
    CascadeLevel(name="system",   profile_ref="builtin://system-defaults", overrides=None),
    CascadeLevel(name="workflow", profile_ref="vault://kv/sagewai/billing-pipeline", overrides=None),
    CascadeLevel(name="user",     profile_ref="onepassword://Sagewai/customer-acme", overrides={"DEBUG": "1"}),
]
```

Per-key merge applies regardless of backend. The cascade resolver dispatches each level's `get_profile` call to the registered backend for that scheme.

---

## Where capabilities live

The following table maps capabilities to the backend type that owns them:

| Capability | Sandbox backend | Identity backend |
|---|:---:|:---:|
| Where the sandbox container/pod/function runs | ✓ | — |
| Container image lifecycle | ✓ | — |
| Network policy enforcement | ✓ | — |
| Resource limits (CPU, memory) | ✓ | — |
| Pool warmth model | ✓ | — |
| `cleanup_run` semantics | ✓ | — |
| Where Tier-2 secret values originate | — | ✓ |
| Master-key management (for builtin) or external trust root (Vault, SOPS) | — | ✓ |
| Profile CRUD UI surface | — | ✓ |
| Cascade resolution semantics (per-key merge, tombstones, drift detection) | — | ✓ (resolver consults the backends) |
| Audit event vocabulary (`secret.revoked`, `profile.injected`, …) | — | ✓ |

These are independent. Changing a Sealed-ii backend does not affect the sandbox topology; changing a sandbox backend does not affect Sealed semantics.

---

## Anti-patterns

1. **Conflating "backend"** in conversation. Always say "Sandbox backend" or "Identity backend" — never just "backend." The two are independent and confusion costs hours.

2. **Forcing one Sandbox backend across the fleet.** Heterogeneous fleets are supported; using only one backend everywhere because "that's how we started" leaves performance and cost wins on the table.

3. **Building features that assume a specific Sandbox backend.** Plan 1.5 (pooling), Sealed-iii.A (cleanup_run), Sealed-iii.B (redaction), etc. all sit on the abstract Protocol. New features should specify behavior in terms of the Protocol, not in terms of `docker exec`.

4. **Backing Tier-2 secrets in something the Identity backend doesn't natively support.** "We'll just write our own Vault wrapper" is a Sealed-ii proposal — it goes through the Sealed-ii decomposition spec, not as a side-project in another plan.

5. **Replicating Identity backends in the worker process.** The worker calls `SealedSecretProvider.env_for(...)` and gets a dict; it does NOT cache profile values, does NOT replicate Vault credentials, does NOT decrypt builtin backend's profile.json. Each `env_for` call goes through the registered backend.

---

## Decision: which Sandbox backend should this deployment use?

```
Are you on a single VM / laptop?
  └── YES → DockerBackend.

Do you have an existing Kubernetes cluster?
  └── YES → KubernetesBackend (production scale + multi-tenant ready).

Is your workload primarily short, sporadic Mode 1/2 jobs (event-driven)?
  └── YES → LambdaBackend for those jobs (the Mode 3 portion still needs Docker/K8s).

Do you need workloads beyond OCI-container isolation?
  └── YES → wait for FirecrackerBackend (or accept the gap; Docker + K8s are the supported defaults).
```

For Identity backend selection, see the Sealed-ii decomposition spec when it exists.

---

## Forward dependencies

| Plan | Depends on |
|---|---|
| Plan 1.5 sandbox pooling | DockerBackend (initial); abstract design over the Protocol so KubernetesBackend support is additive |
| Plan SBX-K8S Kubernetes backend | This doc + execution-modes.md + the SandboxBackend Protocol |
| Plan SBX-LAMBDA Lambda backend | This doc + execution-modes.md (Mode 1/2 only) |
| Sealed-ii (Vault, 1Password, AWS SM, SOPS, Bitwarden) | Sealed-i ProfileBackend Protocol (already shipped); decompose into per-backend sub-specs |
| Sealed-iv (HITL + JIT) | Mode 3b — supported on Docker + K8s (not Lambda) |
