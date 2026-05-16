# Contributing to Sagewai

Thank you for your interest in contributing to Sagewai. This guide covers the
contribution workflow. For local environment setup, see
**[DEVELOPMENT.md](DEVELOPMENT.md)**.

## Getting set up

Full prerequisites and setup instructions live in
**[DEVELOPMENT.md](DEVELOPMENT.md)**. The short version:

```bash
git clone https://github.com/sagewai/platform.git
cd platform
just bootstrap     # install tools, sync all deps, set up git hooks
just smoke         # fast sanity check
```

Run `just` to list every recipe, or `just prereqs` to verify your toolchain.

## Running tests and checks

Run these before opening a PR:

```bash
just test          # full SDK unit suite
just smoke         # fast smoke tests
just lint          # ruff across sdk + admin + docs + vscode
just format        # black (SDK)
just typecheck     # mypy (SDK)
```

Package-scoped variants exist too — `just sdk-test`, `just sdk-lint`,
`just admin-e2e`, and more. See `just --list`.

## Code style

- **Line length**: 100 characters (enforced by black and ruff)
- **Imports**: absolute, sorted by ruff (standard lib, third-party, local)
- **Type hints**: required on all public function signatures
- **Docstrings**: Google-style, required for public classes and functions
- **Async-first**: prefer `async def` for any function that performs I/O
- **HTTP client**: use `httpx.AsyncClient` (never `requests`)
- **Data models**: Pydantic v2 (`@field_validator`, not the deprecated v1 `@validator`)

## Making changes

### Branch naming

```
<issue-number>/<kebab-case-description>
```

Example: `42/add-memory-module`

### Commit messages

Conventional commits with a scope that matches the area you touched:

```
type(scope): description (#issue-number)
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `style`, `perf`

**Scopes** match the area — e.g. `sdk`, `admin`, `cli`, `api`, `docs`, `fleet`,
`memory`, `autopilot`, `backend`, `vscode-extension`, `ci`.

Examples:

- `feat(sdk): add streaming support for chat (#123)`
- `fix(admin): correct budget calculation for daily limits (#456)`
- `docs(sdk): update memory module quickstart (#789)`

### Pull request process

1. Fork the repository (external contributors) or branch directly (maintainers)
2. Create a branch from `main`
3. Make your changes, with tests
4. Run `just test`, `just lint`, and `just typecheck`
5. Push your branch and open a PR; link the relevant issue
6. Wait for review — `main` requires a code-owner review and linear history

### What makes a good PR

- Focuses on a single change
- Includes tests for new functionality
- Updates documentation if the public API changes
- Passes CI
- Has a clear description of what and why

## Repository layout

```
packages/sdk/            Python SDK (sagewai on PyPI)
  sagewai/               core, engines, strategies, workflows, admin, CLI
  tests/                 pytest suite
  sagewai/examples/      runnable examples
packages/tool-runner/    sandboxed tool-execution runner
apps/admin/              Next.js admin dashboard
apps/docs/               Next.js documentation site
apps/backend/            Dockerfile wrapping the SDK
apps/vscode-extension/   VS Code extension
scripts/                 bootstrap, deploy, release scripts
brand/                   logos, icons, favicon (source of truth)
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for a fuller tour.

## Adding a new module

1. Create the module under the appropriate `packages/sdk/sagewai/` subpackage
2. Add exports to the relevant `__init__.py`
3. Write tests under `packages/sdk/tests/`
4. Add the AGPL-3.0-or-later license header to every new `.py` file — copy the
   exact header from an existing file rather than writing a new variant
5. Update `sagewai/__init__.py` if you are adding public API exports

## Licensing of contributions

Sagewai is licensed under **AGPL-3.0-or-later**. By submitting a contribution
you agree that it is licensed under those terms — see [LICENSE](LICENSE).

The project also uses a **Contributor License Grant**: see **[CLA.md](CLA.md)**.
You keep full copyright of your contribution while granting the maintainer a
broad license to use and relicense it — the same pattern used by Cal.com,
Ghost, and MariaDB. No CLA bot is wired up yet; the first external contribution
triggers a brief CLA review before merge.

## Questions?

- Open a [GitHub Discussion](https://github.com/sagewai/platform/discussions) for questions
- File a [GitHub Issue](https://github.com/sagewai/platform/issues) for bugs or feature requests
- Email: hello@sagewai.ai
