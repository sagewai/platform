# Releasing `sagewai-tool-runner`

The package auto-publishes to PyPI on every `vX.Y.Z` tag push via
`.github/workflows/release-tool-runner.yml`.

## One-time Trusted Publisher setup

1. Create the project on PyPI manually the first time (can be a placeholder wheel):
   `uv build --package sagewai-tool-runner` locally, upload via `uv publish`.
2. On https://pypi.org/manage/project/sagewai-tool-runner/settings/publishing/ :
   - Add a new trusted publisher
   - Owner: `sagewai`
   - Repository: `platform`
   - Workflow filename: `release-tool-runner.yml`
   - Environment: `pypi`
3. In the repo on GitHub: Settings → Environments → New environment `pypi`.
   - Optionally add required reviewers.

After this, `release-tool-runner.yml` pushes on each vX.Y.Z tag with no API token.

## Dry run on a pre-release tag

```bash
git tag v0.0.0-rc.1
git push origin v0.0.0-rc.1
```

Watch the workflow. Confirm `sagewai-tool-runner==0.0.0rc1` lands on PyPI.
Then delete the tag and release if you want to keep history clean.
