# Contributing to Sagewai

Thank you for your interest in contributing to Sagewai. This guide covers everything you need to get started.

## Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/) package manager
- Git

## System Requirements

- **Minimum:** Python 3.10+, 512 MB RAM (SDK only, cloud APIs)
- **Development:** 2 GB RAM with PostgreSQL + Redis, or 8 GB+ for full Docker stack
- **GPU:** Only needed for local inference (Ollama, vLLM, Unsloth) — not required for cloud APIs
- See [Hardware Requirements](https://docs.sagewai.ai/docs/guides/hardware-requirements) for detailed profiles

## Non-Docker Setup

If you prefer not to use Docker, or want to use an alternative container runtime:

**Podman (drop-in replacement):**
```bash
brew install podman podman-compose   # macOS
alias docker=podman
alias docker-compose=podman-compose
```

**Native PostgreSQL + Redis:**
```bash
brew install postgresql@15 redis     # macOS
brew services start postgresql@15
brew services start redis
createdb sagecurator
export DATABASE_URL=postgresql://localhost:5432/sagecurator
export REDIS_URL=redis://localhost:6379
```

See [Infrastructure Management](https://docs.sagewai.ai/docs/guides/infrastructure) for full native setup, Podman details, and data volume management.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/sagewai/sagewai.git
cd sagewai

# Install all dependencies
uv sync --all-packages

# Verify the installation
uv run --package sagewai python -c "import sagewai; print(sagewai.__version__)"
```

## Running Tests

```bash
# Run all unit tests
uv run --package sagewai --with pytest --with pytest-asyncio \
  pytest packages/sagewai/tests/ --override-ini="addopts=" \
  --ignore=packages/sagewai/tests/integration

# Run a specific test file
uv run --package sagewai --with pytest --with pytest-asyncio \
  pytest packages/sagewai/tests/test_base.py --override-ini="addopts="

# Run with full dependencies (recommended)
uv sync --all-packages
.venv/bin/pytest packages/sagewai/tests/ --override-ini="addopts=" \
  --ignore=packages/sagewai/tests/integration
```

## Code Style

We use these tools — run them before submitting a PR:

```bash
# Format code
black packages/sagewai --line-length 100

# Lint
ruff check packages/sagewai

# Type check
mypy packages/sagewai/sagewai
```

### Style Rules

- **Line length**: 100 characters (enforced by black and ruff)
- **Imports**: absolute imports, sorted by ruff (standard lib, third-party, local)
- **Type hints**: required on all public function signatures
- **Docstrings**: Google-style, required for public classes and functions
- **Async-first**: prefer `async def` for any function that performs I/O
- **HTTP client**: use `httpx.AsyncClient` (never `requests`)
- **Data models**: Pydantic v2 (use `@field_validator`, not deprecated v1 `@validator`)

## Making Changes

### Branch Naming

```
<issue-number>/<kebab-case-description>
```

Example: `42/add-memory-module`

### Commit Messages

Use conventional commits with scope:

```
type(scope): description (#issue-number)
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `style`, `perf`

Scopes: `sdk`, `harness`, `registry`, `observatory`, `training`, `admin`, `cli`, `docs`

Examples:
- `feat(sdk): add streaming support for chat (#123)`
- `fix(harness): correct budget calculation for daily limits (#456)`
- `docs(sdk): update memory module quickstart (#789)`

### Pull Request Process

1. Fork the repository
2. Create a branch from `main`
3. Make your changes
4. Run tests and linting
5. Push your branch and open a PR
6. Link the relevant issue in the PR description
7. Wait for review

### What Makes a Good PR

- Focuses on a single change
- Includes tests for new functionality
- Updates documentation if the public API changes
- Passes all CI checks
- Has a clear description of what and why

## Project Structure

```
packages/sagewai/
  sagewai/           # SDK source code
    core/            # Agents, workflows, strategies
    engines/         # LLM engine implementations
    harness/         # LLM proxy and routing
    memory/          # Vector and graph memory
    context/         # Document ingestion and retrieval
    safety/          # Guardrails, PII, permissions
    connectors/      # Built-in tool connectors
    ...
  tests/             # Test suite
```

## Adding a New Module

1. Create the module in the appropriate directory
2. Add exports to the relevant `__init__.py`
3. Write tests in `tests/test_<module>.py`
4. Add the AGPL-3.0 license header to all new `.py` files
5. Update `sagewai/__init__.py` if adding public API exports

## License

By contributing to Sagewai, you agree that your contributions will be licensed under the AGPL-3.0 license. See the [LICENSE](LICENSE) file for details.

No Contributor License Agreement (CLA) is required.

## Questions?

- Open a [GitHub Discussion](https://github.com/sagewai/sagewai/discussions) for questions
- File a [GitHub Issue](https://github.com/sagewai/sagewai/issues) for bugs or feature requests
- Email: hello@sagewai.ai
