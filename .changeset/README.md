# Changesets

This directory is the release queue for the Sagewai monorepo. Every
user-facing change should land with a changeset file that describes the
change in plain English and declares its semver impact.

## Authoring a changeset

From the repo root:

```bash
pnpm changeset
```

An interactive prompt asks which packages changed and whether the change
is `patch`, `minor`, or `major`. Write a one-sentence summary in
changelog-ready prose ("Add support for X", not "fix: update X"). The
tool writes a new `.changeset/<slug>.md` file — commit it alongside your
code change.

## Unified versioning

All publishable packages are bound together via the `fixed` array in
`config.json`. That means one changeset always bumps **every** package
to the same version — the next release tag will update:

- `sagewai` on PyPI (from `packages/sdk/pyproject.toml`)
- `@sagewai/admin` Docker image on GHCR (from `apps/admin/package.json`)
- `@sagewai/docs` deploy on Cloudflare Pages (from `apps/docs/package.json`)
- `sagewai` VS Code extension on Marketplace (from `apps/vscode-extension/package.json`)
- `sagewai-backend` Docker image on GHCR (mirrored from the SDK version)

Pick the biggest semver impact across all of them when authoring — if
the change only touches docs but would be a `patch`, pick `patch`; the
fixed array propagates that bump to everyone.

## Releasing

Maintainers only:

```bash
./scripts/release.sh
git push origin main --tags
```

The `vX.Y.Z` tag push triggers `.github/workflows/release-*.yml` which
publishes all artifacts in parallel.
