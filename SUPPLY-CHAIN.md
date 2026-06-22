# Supply Chain Security

This document describes how Sagewai manages dependencies, verifies
integrity, and prevents supply chain attacks.

## Dependency Policy

1. **Exact version pins.** Every dependency in every manifest uses exact
   versions (`==` for Python, no `^` or `~` for npm). Lock files
   (`uv.lock`, `pnpm-lock.yaml`) are committed and enforce reproducible
   builds. Floating ranges are never acceptable.

2. **Minimal dependencies.** Every dependency must justify its presence.
   Before adding a new dependency, consider: can this be done with the
   standard library? Is the package actively maintained? What is its
   transitive dependency tree?

3. **Latest stable versions.** Dependencies are kept current through
   monthly, grouped Dependabot PRs (minor/patch bumps are batched per
   ecosystem; majors open individually so risky upgrades stay reviewable).
   Every update goes through CI (tests, audit, license check) before merge.

4. **SHA-pinned GitHub Actions.** All CI/CD actions are pinned to
   immutable commit SHAs, not mutable tags. A version comment is added
   for readability (e.g., `actions/checkout@<sha> # v6`).

5. **Digest-pinned Docker images.** Base images in Dockerfiles use
   `@sha256:...` digests, not floating tags like `python:3.12-slim`.

## License Allowlist

Only dependencies with these SPDX licences are permitted:

- MIT
- Apache-2.0
- BSD-2-Clause
- BSD-3-Clause
- ISC
- PSF-2.0 / Python-2.0
- MPL-2.0
- Unlicense
- 0BSD

Any dependency with a different licence requires explicit approval
documented in this file.

### Approved Exceptions

| Package | Licence | Reason |
|---------|---------|--------|
| (none yet) | | |

## Core Dependency Additions

New runtime deps added for tool-catalog batch 1 (no-auth tier tools).

| Package | Version | Licence | Purpose |
|---------|---------|---------|---------|
| duckduckgo-search | 8.1.1 | MIT | DuckDuckGo backend for `web_search` tool |
| pypdf | 6.11.0 | BSD-3-Clause | PDF text extraction for `pdf_parse` tool |
| readability-lxml | 0.8.4.1 | Apache-2.0 | Main-content extraction for `web_scrape` tool |

Deps promoted to base for durable-persistence (PR2a — SQLite default):

| Package | Version | Licence | Purpose |
|---------|---------|---------|---------|
| sqlalchemy[asyncio] | 2.0.49 | MIT | ORM/Core async DB layer; was `[postgres]`-only, now base (SQLite default) |
| aiosqlite | 0.22.1 | MIT | Async SQLite driver for SQLAlchemy's asyncio extension; new direct dep |

Deps added for durable-persistence (PR3.1 — sqlite-vec vector memory):

| Package | Version | Licence | Purpose |
|---------|---------|---------|---------|
| sqlite-vec | 0.1.9 | MIT | SQLite extension for durable local vector memory (vec0 virtual tables + KNN queries); zero-config default vector path |

## Optional Dependency Inventory

Extras (opt-in via `pip install sagewai[<extra>]`) documented below for
review during dependency updates.

| Package | Version | Licence | Extra | Role |
|---------|---------|---------|-------|------|
| hvac | 2.3.0 | MIT | vault | HashiCorp Vault community client |

## Admin UI New Dependencies (Plan P)

| Package | Version | Licence | Type | Role |
|---------|---------|---------|------|------|
| canvas-confetti | 1.9.3 | MIT | prod | First-mission celebration burst |
| @types/canvas-confetti | 1.9.0 | MIT | dev | TypeScript types for canvas-confetti |
| @axe-core/playwright | 4.10.2 | MPL-2.0 | dev | Automated WCAG AA accessibility tests |

## CI Security Gates

Every PR runs these checks automatically:

| Check | Tool | Fails on |
|-------|------|----------|
| Python vulnerability audit | `pip-audit --strict` | Any known CVE |
| JS vulnerability audit | `pnpm audit --audit-level=high` | High or critical CVE |
| Python licence check | `pip-licenses --allow-only=<allowlist>` | Unlisted licence |
| Static analysis | `ruff check`, TypeScript `noEmit` | Lint violations |

## Update Workflow

### Routine Updates (monthly via Dependabot)

1. Dependabot opens a PR with the version bump.
2. CI runs tests, audit, and licence check.
3. Maintainer reviews the diff and changelog.
4. If CI passes and the changelog is clean, merge.
5. Update `SUPPLY-CHAIN.md` if the dependency is new or the licence changed.

### Adding a New Dependency

1. Check the package on [Snyk Advisor](https://snyk.io/advisor/) or
   [Socket.dev](https://socket.dev/) for security score.
2. Verify the licence is on the allowlist above.
3. Add with an exact version pin.
4. Run `pip-audit` / `pnpm audit` locally.
5. Document the dependency's purpose in a commit message.

### Emergency: Compromised Dependency

1. **Identify** the affected package and versions.
2. **Pin** to the last known-good version immediately.
3. **Audit** whether the compromised version was deployed.
4. **Notify** via security@sagewai.ai if user data could be affected.
5. **Report** upstream and to the relevant CVE database.
6. **Document** the incident in this file under "Incident Log".

## SBOM (Software Bill of Materials)

Every release generates a CycloneDX SBOM as a build artifact, attached
to the GitHub Release. This provides a machine-readable inventory of
all components, their versions, and licences.

## Artifact Attestation

Release workflows use GitHub's built-in artifact attestation to
cryptographically link artifacts (PyPI wheels, Docker images) to their
source code and build environment. Consumers can verify provenance
using `gh attestation verify`.

## Incident Log

| Date | Package | Versions | Impact | Resolution |
|------|---------|----------|--------|------------|
| 2026-05-30 | python-multipart | 0.0.20 → 0.0.27 | 3 CVEs (multipart parsing DoS) in the file-upload request path | Bumped pin in the `[fastapi]` extra + test group; re-locked, full suite green |
| 2026-05-30 | starlette | 1.0.0 → 1.2.0 | PYSEC-2026-161 (ASGI framework) | Transitive bump via `uv lock --upgrade-package`; full suite green |
| 2026-05-30 | idna | 3.11 → 3.17 | CVE-2026-45409 | Transitive bump via `uv lock --upgrade-package` |
| 2026-05-30 | lxml | 6.0.3 → 6.1.1 | PYSEC-2026-87 (XML parsing) | Transitive bump via `uv lock --upgrade-package` |
| 2026-05-30 | mako | 1.3.10 → 1.3.12 | CVE-2026-44307 | Transitive bump via `uv lock --upgrade-package` |
| 2026-06-07 | pyjwt | 2.12.1 → 2.13.0 | PYSEC-2026-175/177/178/179 (4 CVEs in the JWT/auth-token path) | Bumped base-dep pin; re-locked; auth/jwt/gateway/oauth suites green on the new version |
| 2026-06-07 | aiohttp | 3.13.4 → 3.14.0 | CVE-2026-34993, CVE-2026-47265 | Transitive bump via `uv lock --upgrade-package aiohttp` (parents: litellm/aiodocker/kubernetes-asyncio); full suite green |
| 2026-06-22 | cryptography | 48.0.0 → 48.0.1 | GHSA-537c-gmf6-5ccf (credential/secret encryption + TLS dep) | Bumped base-dep pin in `packages/sdk/pyproject.toml`; re-locked; `pip-audit` clean, smoke green |
| 2026-06-22 | pypdf | 6.13.0 → 6.13.3 | GHSA-jm82-fx9c-mx94 (PDF text extraction — `pdf_parse` tool) | Bumped base-dep pin; re-locked; `pip-audit` clean, smoke green |
| 2026-06-22 | aiohttp | 3.14.0 → 3.14.1 | 8 CVEs (CVE-2026-54273 / 54274 / 54275 / 54276 / 54277 / 54278 / 54279 / 54280) | Transitive bump via `uv lock --upgrade-package aiohttp` (parents: litellm/aiodocker/kubernetes-asyncio); surfaced by `pip-audit` while fixing the above; gate now clean |
| 2026-06-22 | starlette | 1.2.0 → 1.3.1 | CVE-2026-54282, CVE-2026-54283 (ASGI framework) | Transitive bump via `uv lock --upgrade-package starlette` (parents: fastapi/sse-starlette); surfaced by `pip-audit`; gate now clean |

## Tools

- [pip-audit](https://pypi.org/project/pip-audit/) — Python vulnerability scanner
- [pnpm audit](https://pnpm.io/cli/audit) — npm vulnerability scanner
- [pip-licenses](https://pypi.org/project/pip-licenses/) — Python licence checker
- [Dependabot](https://docs.github.com/en/code-security/dependabot) — automated dependency updates
- [GitHub Artifact Attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) — build provenance
