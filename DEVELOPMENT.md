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
just smoke         # run the actual software Work sanity path
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
just smoke          # software Work smoke, ~1s, no external deps
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

### Software Work verification image

Software Work verification never runs project commands on the worker host. Set
`SAGEWAI_WORK_VERIFICATION_IMAGE` to an immutable digest-pinned Docker image,
for example `registry.example/verifier@sha256:<64 hexadecimal characters>`.

The image must contain the Sagewai tool runner plus every tool and dependency
needed by the configured verification commands. The default command is `just
smoke`, so that image needs `just`, `uv`, Python 3, Node.js 20 or newer, and
the repository's locked test environment. Root smoke invokes `node` directly
and does not require npm. Verification runs with no network, no inherited
environment, a read-only container root, bounded CPU/memory/processes, and a
disposable copy of the Work workspace. Ignored host files and the worker's Codex,
Claude, and Sagewai credentials are not mounted. There is no host-execution
fallback.

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

macOS may resolve `localhost` to `::1` (IPv6) while `127.0.0.1` is
IPv4-only. Normal local development uses the CLI defaults and opens the
admin UI at `http://localhost:3008`. If the UI remains on “Connecting to
server…” while the backend is bound to `127.0.0.1`, run `just dev-all`
(which binds the backend to `0.0.0.0`) or point `NEXT_PUBLIC_ADMIN_API_URL`
at `http://127.0.0.1:8000/admin`.

The Playwright E2E stack instead pins the backend, frontend, browser
base URL, and `NEXT_PUBLIC_ADMIN_API_URL` to `127.0.0.1` on ports 18000
and 3808. Keep those E2E endpoints consistent when changing the test
configuration.

## Known Issues

1. **No `/health` route on the admin FastAPI.** The Dockerfile healthcheck hits `/openapi.json` as a proxy.
2. **VS Code extension has no `build` script yet.** `just vscode-build` is a safe no-op.
