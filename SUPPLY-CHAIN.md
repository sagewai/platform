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
   weekly Dependabot PRs. Every update goes through CI (tests, audit,
   license check) before merge.

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

## Optional Dependency Inventory

Extras (opt-in via `pip install sagewai[<extra>]`) documented below for
review during dependency updates.

| Package | Version | Licence | Extra | Role |
|---------|---------|---------|-------|------|
| hvac | 2.3.0 | MIT | vault | HashiCorp Vault community client |

## CI Security Gates

Every PR runs these checks automatically:

| Check | Tool | Fails on |
|-------|------|----------|
| Python vulnerability audit | `pip-audit --strict` | Any known CVE |
| JS vulnerability audit | `pnpm audit --audit-level=high` | High or critical CVE |
| Python licence check | `pip-licenses --allow-only=<allowlist>` | Unlisted licence |
| Static analysis | `ruff check`, TypeScript `noEmit` | Lint violations |

## Update Workflow

### Routine Updates (weekly via Dependabot)

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
| (none) | | | | |

## Tools

- [pip-audit](https://pypi.org/project/pip-audit/) — Python vulnerability scanner
- [pnpm audit](https://pnpm.io/cli/audit) — npm vulnerability scanner
- [pip-licenses](https://pypi.org/project/pip-licenses/) — Python licence checker
- [Dependabot](https://docs.github.com/en/code-security/dependabot) — automated dependency updates
- [GitHub Artifact Attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) — build provenance
