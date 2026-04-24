# Workflow setup notes

## SAGEWAI_BOT_PAT

Required by `release-sandbox.yml` for the `manifest-and-commit` job to push
updates to `packages/sdk/sagewai/sandbox/image_manifest.py` on `main`.

- Create a fine-scoped GitHub PAT: `contents: write` on `sagewai/platform` only
- Add as an Actions secret at repo Settings → Secrets → Actions
- The default GITHUB_TOKEN cannot push to protected `main`
