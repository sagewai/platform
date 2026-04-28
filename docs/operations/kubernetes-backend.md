# Operating Sagewai on Kubernetes

This guide walks an operator through setting up a Sagewai worker that uses
`KubernetesBackend` for sandbox isolation.

## Prerequisites

- A Kubernetes cluster (1.26+; tested on kind 0.23 / k8s 1.30, AWS EKS, GKE).
- A namespace for sandbox pods (default: `sagewai`).
- A worker host (or in-cluster pod) with `pip install sagewai[kubernetes]`.
- The `sagewai/sandbox-base:dev` (or production-tagged) image available to the
  cluster (loaded into kind, pushed to a registry the cluster can pull from, etc.).

## Required RBAC

The worker's ServiceAccount (or kubeconfig user) needs:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: sagewai-worker
  namespace: sagewai
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/exec", "pods/log", "pods/status"]
    verbs: ["create", "get", "list", "watch", "patch", "delete"]
  - apiGroups: ["apps"]
    resources: ["deployments", "deployments/scale"]
    verbs: ["create", "get", "list", "watch", "patch", "delete"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["create", "get", "list", "patch"]
```

If the worker cannot manage NetworkPolicy resources, `pool.start()` logs a
WARN and continues — operator must apply the three NPs manually (see the
`build_network_policies` helper in `sagewai.sandbox.k8s_resources` for the
canonical YAML, or the architecture doc's "NetworkPolicy resources" section).

## Cluster setup commands

For a local kind cluster:

```bash
kind create cluster --name sagewai-prod
kubectl create namespace sagewai

# Pre-pull or load the sandbox image
docker pull ghcr.io/sagewai/sandbox-base:dev   # or build locally
kind load docker-image ghcr.io/sagewai/sandbox-base:dev --name sagewai-prod
```

## Configure the backend

```bash
sagewai admin sandbox config k8s \
  --kubeconfig ~/.kube/config \
  --namespace sagewai \
  --egress-allowlist 10.0.0.0/8,192.168.0.0/16 \
  --verify
```

`--verify` runs a `health_check` against the cluster after writing config.
Output:

```
wrote sandbox_backends.kubernetes to ~/.sagewai/admin-state.json
health: ok=True server=v1.30.0
```

## Start the worker

```bash
sagewai workflow worker --sandbox-backend kubernetes
```

The worker:
1. Reads `~/.sagewai/admin-state.json` for the kubernetes config.
2. Constructs `KubernetesBackend` + `ExternalMinReplicasSandboxPool`.
3. `pool.start()` applies the three NetworkPolicies (or warns on RBAC denial).
4. Advertises `sandbox.backend=kubernetes` to the fleet registry.
5. Begins claiming runs that match its capabilities.

## Verifying the deployment

After the worker has handled at least one Mode 1+ run:

```bash
kubectl get deployments -n sagewai -l sagewai.io/managed-by=sagewai
# sagewai-pool-<hash>   N/N   N        N        Xs

kubectl get networkpolicies -n sagewai -l sagewai.io/managed-by=sagewai
# sagewai-netpol-none
# sagewai-netpol-egress-allowlist
# sagewai-netpol-full

kubectl get pods -n sagewai -l sagewai-pool
# Mix of phase=warm (deployment-managed) and phase=leased (active runs)
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pod stuck `Pending` with `ImagePullBackOff` | Cluster cannot reach image registry | Push to a reachable registry, or load locally with `kind load docker-image` |
| Pod stuck `Pending` with `FailedScheduling` | No nodes have capacity | Scale node group; check `kubectl describe node` |
| Pool `start()` WARN about NetworkPolicy 403 | Worker SA lacks `networking.k8s.io/networkpolicies` write | Grant the Role above, or hand-write the three NetworkPolicies and `kubectl apply -f` them |
| Conformance tests SKIPPED | `SAGEWAI_K8S_TEST_KUBECONFIG` not set | Export it pointing at a reachable kubeconfig file |
| Curl from `network_policy=NONE` pod still works | Cluster CNI doesn't enforce NetworkPolicy (e.g., minikube + Docker driver) | Use kind with kindnet, or install Calico / Cilium |

## Differences from `DockerBackend`

| Capability | Docker | Kubernetes |
|---|---|---|
| Pod lifecycle | container | Pod (orphan-on-claim) |
| Pool warmth | `LocalCacheSandboxPool` | `ExternalMinReplicasSandboxPool` (Deployment) |
| `EGRESS_ALLOWLIST` enforcement | Plan 3d egress proxy (planned) | Native via NetworkPolicy CIDRs (this plan) |
| Multi-host scaling | one host | cluster autoscaler |
| `cleanup_run` | in-memory `set_env({})` | identical (Plan 1.5 model) |

See [../architecture/execution-backends.md](../architecture/execution-backends.md)
for the full backend taxonomy and the
[Plan SBX-K8S design spec](../superpowers/specs/2026-04-27-sandbox-k8s-backend-design.md)
for implementation detail.
