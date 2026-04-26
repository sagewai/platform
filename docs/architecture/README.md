# Sagewai architecture

Authoritative reference for Sagewai's runtime structure, security model, and pluggable backends.

Read in order:

1. **[runtime-topology.md](runtime-topology.md)** — control plane vs worker fleet; the three invariants; component diagram; per-mode data flow.
2. **[security-tiers.md](security-tiers.md)** — Tier-1 (orchestration, host-side) vs Tier-2 (user-task, sandbox-side) keys; what Sagewai sees vs doesn't see; trust assumption summary.
3. **[execution-modes.md](execution-modes.md)** — the five modes (0, 1, 2, 3, 3b); cost/security trade-offs; per-step mode selection.
4. **[execution-backends.md](execution-backends.md)** — pluggable Sandbox backends (Docker / K8s / Lambda / Null) and Identity backends (Builtin / Vault / 1Password / AWS SM / SOPS / Bitwarden); mode × backend compatibility matrix.

Every implementation plan and spec is checked against these four docs. If something in a plan diverges from this architecture, fix the doc OR fix the plan — never both at once without explicit reconciliation.

## User-facing render

The docs site at <https://docs.sagewai.ai> mirrors these four documents in user-facing tone under the **Architecture** section (5 pages: overview + the four chapters). Internal jargon (`Sealed-iii.A`, `Plan 1.5`, …) is replaced with descriptive prose; the conceptual model is identical. Sources:

- `apps/docs/app/docs/architecture/page.mdx` — section index
- `apps/docs/app/docs/architecture/runtime-topology/page.mdx`
- `apps/docs/app/docs/architecture/security-tiers/page.mdx`
- `apps/docs/app/docs/architecture/execution-modes/page.mdx`
- `apps/docs/app/docs/architecture/sandbox-backends/page.mdx` (URL slug differs from this filename — operators search "sandbox backends," not "execution backends")

When you change one of the four canonical docs in this directory, update the corresponding page in `apps/docs/app/docs/architecture/` in the same PR. The `docs(architecture):` PR-title convention catches both.

## Change procedure

1. Open a PR titled `docs(architecture): <doc-name> — <one-line summary>`.
2. CODEOWNERS review.
3. If the change invalidates an in-flight feature plan, mention that plan's PR or design doc in the body so dependent work can update.
4. Merge. Subsequent threads see the new contract on next start.
