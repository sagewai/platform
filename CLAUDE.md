# CLAUDE.md

**Read [AGENTS.md](./AGENTS.md) — it's the source of truth for this repo.** Project layout, common tasks, conventions, and releases all live there; this file exists only so Claude Code finds its way to it.

How to work here, in five lines (full version in AGENTS.md):

- **Think before coding** — state assumptions, ask when unclear, surface tradeoffs.
- **Simplicity first** — the minimum that solves the problem; nothing speculative.
- **Surgical changes** — touch only what the task needs; don't refactor adjacent code.
- **Tests first** — failing test, then the minimal code to pass it; keep `just smoke` fast.
- **Branch names** — `[feat/bug/chore]-[issue-id]-[short-definition]`; never the auto `claude/<slug>` form (omit `issue-id` when there's no issue).
