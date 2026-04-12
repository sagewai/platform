# Development Guide

Everything you need to get the Sagewai platform running locally.

## Prerequisites

| Tool | Minimum | Install |
|------|---------|---------|
| **Python** | 3.10+ | `brew install python@3.12` or [python.org](https://www.python.org/downloads/) |
| **uv** | 0.5+ | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node.js** | 20+ | `brew install node@22` or [nvm](https://github.com/nvm-sh/nvm) |
| **pnpm** | 10+ | Installed automatically by `just bootstrap` via corepack |
| **just** | 1.0+ | `brew install just` or `cargo install just` |
| **Docker** | 20+ | Optional — only needed for `just compose-up` / `just backend-build`. Podman works too. |

Run `just prereqs` to verify everything is installed.

## Quick Start

```bash
just bootstrap     # install tools, sync all deps, set up git hooks
just smoke         # run 29 fast tests to confirm everything works
just dev-all       # start backend (:8000) + admin UI (:3008)
```

## Repository Layout

```
packages/sdk/          Python SDK (sagewai on PyPI)
  sagewai/             core engines, strategies, workflows, admin, CLI
  tests/               pytest suite (2904 tests)
  sagewai/examples/    23 runnable examples
apps/admin/            Next.js admin dashboard (port 3008)
apps/docs/             Next.js documentation site (port 3010)
apps/backend/          Dockerfile wrapping the SDK
apps/vscode-extension/ VS Code extension
scripts/               bootstrap, deploy, release scripts
brand/                 logos, icons, favicon (source of truth)
```

## Common Workflows

### Testing

```bash
just smoke          # 29 smoke tests, ~1s, no external deps
just test           # full suite, 2904 tests, ~14s
just perf           # performance micro-benchmarks
just sdk-test       # SDK tests only
```

### Building

```bash
just build          # build all: SDK wheel + admin + docs + vscode
just sdk-build      # SDK wheel + sdist only
just admin-build    # admin production bundle
just docs-build     # docs static export
just backend-build  # backend Docker image (local)
```

### Linting & Formatting

```bash
just lint           # lint all packages
just format         # format Python code (ruff)
just typecheck      # type-check Python code (mypy)
just sdk-lint       # SDK only
```

### Dev Servers

```bash
just dev-all        # backend + admin concurrently
just admin-dev      # admin UI only (port 3008)
just docs-dev       # docs site only (port 3010)
```

### Docker

```bash
just compose-up     # full stack: postgres + redis + backend + admin
just compose-down   # stop everything
just admin-up       # lightweight: backend + admin via Docker
just backend-build  # build backend image locally
```

### Diagnostics

```bash
just doctor         # check installation health
just status         # check infrastructure connectivity
just prereqs        # verify all dev tools installed
```

## Versioning & Release

Unified semver via [Changesets](https://github.com/changesets/changesets).
One `vX.Y.Z` tag bumps SDK, admin, docs, backend, and vscode-extension
to the same version.

```bash
pnpm changeset              # author a changeset
./scripts/release.sh        # compute version, commit, tag
git push origin main --follow-tags
```

The tag push triggers release workflows for PyPI, GHCR, Cloudflare, and
VS Code Marketplace.

## Dependency Management

See [SUPPLY-CHAIN.md](SUPPLY-CHAIN.md) for the full policy. Key rules:

- **Always use exact versions.** `==X.Y.Z` for Python, `"X.Y.Z"` (no `^`) for npm.
- **Never add a dependency without checking** its licence, maintenance status, and security score.
- **Run audits locally** before committing dependency changes:
  ```bash
  uv run --with pip-audit pip-audit --strict       # Python
  pnpm audit --audit-level=high                     # JavaScript
  ```
- **Dependabot** opens weekly PRs for updates. Review, verify CI passes, merge.
- **Lock files** (`uv.lock`, `pnpm-lock.yaml`) are committed and must not be gitignored.

## macOS: `localhost` vs `127.0.0.1`

macOS resolves `localhost` to `::1` (IPv6) while `127.0.0.1` is IPv4
only. The admin panel's browser-side health check fetches
`http://localhost:8000/...`. If you bind the backend to `--host
127.0.0.1`, those requests hit `::1` and silently fail — the UI shows
"Connecting to server…" forever.

**Always use the CLI default** (`--host 0.0.0.0`, which listens on all
interfaces) and open `http://localhost:3808` (not `127.0.0.1`) in your
browser.

## Known Issues

1. **`sagewai[fastapi]` extra is missing `uvicorn`.** Workaround: `uv pip install uvicorn` after sync.
2. **No `/health` route on the admin FastAPI.** The Dockerfile healthcheck hits `/openapi.json` as a proxy.
3. **VS Code extension has no `build` script yet.** `just vscode-build` is a safe no-op.
