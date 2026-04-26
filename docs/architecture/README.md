# Sagewai architecture

Authoritative reference for Sagewai's runtime structure, security model, and pluggable backends.

Read in order:

1. **[runtime-topology.md](runtime-topology.md)** — control plane vs worker fleet; the three invariants; component diagram; per-mode data flow.
2. **[security-tiers.md](security-tiers.md)** — Tier-1 (orchestration, host-side) vs Tier-2 (user-task, sandbox-side) keys; what Sagewai sees vs doesn't see; trust assumption summary.
3. **[execution-modes.md](execution-modes.md)** — the five modes (0, 1, 2, 3, 3b); cost/security trade-offs; per-step mode selection.
4. **[execution-backends.md](execution-backends.md)** — pluggable Sandbox backends (Docker / K8s / Lambda / Null) and Identity backends (Builtin / Vault / 1Password / AWS SM / SOPS / Bitwarden); mode × backend compatibility matrix.

Every implementation plan and spec is checked against these four docs. If something in a plan diverges from this architecture, fix the doc OR fix the plan — never both at once without explicit reconciliation.

## Change procedure

1. Open a PR titled `docs(architecture): <doc-name> — <one-line summary>`.
2. CODEOWNERS review.
3. If the change invalidates an in-flight feature plan, mention that plan's PR or design doc in the body so dependent work can update.
4. Merge. Subsequent threads see the new contract on next start.
