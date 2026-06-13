# AGENTS.md

The source of truth for AI coding assistants (Claude Code, Cursor, Copilot, Cline, …) working on **sagewai/platform**. If your tool reads one file, read this one — `CLAUDE.md` just points here.

## What this is

`sagewai/platform` is the public monorepo for the Sagewai stack: a Python SDK (`sagewai` on PyPI), a Next.js admin UI, the docs site, a backend Docker image, and a VS Code extension. Companion repos: `sagewai/web` (marketing) and the `sagewai-*` client wrappers.

## Layout

```
packages/sdk/            Python SDK — subpackages: cli, mcp, admin, gateway,
                         fleet, sandbox, sealed, autopilot, harness, connections
apps/admin/              Next.js admin UI
apps/docs/               docs.sagewai.ai (Next.js + Cloudflare)
apps/backend/            Dockerfile wrapping packages/sdk
apps/vscode-extension/   VS Code extension
scripts/                 bootstrap.sh, release.sh, deploy-*.sh
.changeset/              unified versioning
```

## Common tasks

```bash
just bootstrap     # first-time setup (uv + pnpm + workspace sync)
just smoke         # fast smoke tests (sub-second) — the pre-commit check
just test          # full SDK suite
just dev-all       # admin + backend
just compose-up    # full stack (postgres + redis + backend + admin)
```

Package-scoped variants: `just sdk-test`, `just admin-dev`, `just docs-dev`, … Run `just` for the live list. See `DEVELOPMENT.md` for prerequisites.

## How to work here

Bias toward caution over speed; use judgment on trivial tasks.

- **Think before coding.** State your assumptions; if uncertain, ask. If multiple interpretations exist, surface them — don't pick silently. If a simpler approach exists, say so.
- **Simplicity first.** The minimum code that solves the problem — no speculative features, abstractions, or config that wasn't requested. If 200 lines could be 50, rewrite it.
- **Surgical changes.** Touch only what the task needs and match existing style; don't refactor or reformat adjacent code. Remove only the orphans *your* change created — flag pre-existing dead code, don't delete it.
- **Tests first.** Write the failing test, then the minimal code to pass it. Keep `just smoke` sub-second.

## House rules

- **Don't bypass setup.** The admin first-time setup wizard is the security boundary. Never hard-code `setup_required: false`, return placeholder auth tokens, or skip credential verification.
- **Project scoping is the most-tested invariant.** Every entity carries a `project_id` (`null` = org-global, slug = project-scoped). Cross-project leakage is the thing this codebase guards hardest — plumb `project_id` end-to-end for any new entity.
- **Routes live in `admin/serve.py`** (the app factory), not inline in `cli/admin.py` (a thin wrapper).
- **Licensing.** Every `packages/sdk` Python file carries the AGPL-3.0-or-later header — copy the exact phrasing from an existing file.
- **Docs MDX.** In `apps/docs/**/*.mdx`, escape `<digit`/`<lowercase` in prose as `&lt;` (Turbopack reads them as JSX tags); code fences are fine.
- **Issues over half-fixes.** Too deep to fix now? File a GitHub issue — area label (`sdk`/`admin`/`cli`/`fleet`/`docs`/…) + priority (`bug`/`enhancement`/`chore`), titled `[area] short description`.
- **Branch names.** `[feat/bug/chore]-[issue-id]-[short-definition]` — e.g. `feat-123-conn-import-multi`, `bug-456-postgres-url-scheme`, `chore-789-branch-naming`. Never the auto `claude/<slug>` form; omit `issue-id` when there's no tracked issue.

## Releases

Unified semver via Changesets — one tag bumps every package:

```bash
pnpm changeset
./scripts/release.sh
git push origin main --follow-tags
```

## Where to look first

- `README.md` — quickstart + tour
- `docs/architecture/README.md` — canonical runtime architecture
- `packages/sdk/sagewai/examples/` — runnable end-to-end examples
- `packages/sdk/README.md` — SDK API surface
- `.changeset/README.md` — release flow
