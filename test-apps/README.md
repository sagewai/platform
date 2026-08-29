# Sagewai coordinator test applications

These small, dependency-free applications are safe targets for exercising the
Sagewai Work coordinator. Each has a deterministic `just smoke` contract and
suggested bounded changes that let you observe analysis, Codex implementation,
verification, independent Claude review, repair, and durable resume.

| Application | What it exercises | Run locally |
|---|---|---|
| [Signal Runner](./browser-game/) | Deterministic engine; syntax-checked browser UI with keyboard and touch controls | `cd browser-game && npm run smoke && npm run serve` |
| [Approval Desk](./approval-desk/) | Project isolation, evidence gates, immutable decisions, restart-safe SQLite persistence | `cd approval-desk && python3 -m unittest -v` |

Run both suites from the repository root with `just test-apps-smoke`. The normal
`just smoke` contract includes them, so a Sagewai change to either application
is verified by the same root command used for the rest of the repository.

See [Using Sagewai as the coordinator](../docs/operations/work-coordinator.md)
for local, GitHub, Fleet, recovery, and monitoring walkthroughs.
