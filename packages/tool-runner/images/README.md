# Sagewai sandbox images — build context

## For operators (production)

Production deployments pull the published registry images, not local
builds:

```bash
sagewai worker start --sandbox-image ghcr.io/sagewai/sandbox-base:0.1.5
```

Each SDK release pins the exact digest; `DockerBackend` enforces a match.

## For contributors (local builds)

Seven hardened OCI image variants that sit under every Sagewai tool call.
Published to `ghcr.io/sagewai/sandbox-<variant>` on every SDK release
by `.github/workflows/release-sandbox.yml`.

## Variants

| Variant | Purpose | Size (uncompressed) |
|---|---|---|
| `base` | Python + common UNIX tools + `sagewai-tool-runner` | ~180 MB |
| `general` | base + cloud CLIs + web scraping | ~900 MB |
| `ml` | general + pandas / numpy / torch-CPU / polars | ~2.7 GB |
| `ops` | general + kubectl / helm / terraform | ~1.3 GB |
| `erp` | general + SAP / NetSuite / Dynamics / Zoho CLIs | ~1.6 GB |
| `ecommerce` | general + Shopify / Stripe / SFDX / Magento | ~1.4 GB |
| `api` | general + Newman / httpie / k6 / grpcurl / Kong | ~950 MB |

`ml-cuda` ships in Plan 2.1 with GPU CI.

## Building locally

```bash
# Load shared build args
source packages/tool-runner/images/snapshot.env
source packages/tool-runner/images/pins.env

# Build a variant
docker buildx build \
  --file packages/tool-runner/images/base/Dockerfile \
  --build-arg SNAPSHOT_DATE \
  --build-arg PYTHON_DIGEST \
  --build-arg TOOL_RUNNER_VERSION=0.1.0 \
  --tag ghcr.io/sagewai/sandbox-base:dev \
  --load \
  .
```

## Maintenance cadence

| File | How | Cadence |
|---|---|---|
| `snapshot.env`   `SNAPSHOT_DATE`       | manual bump | quarterly |
| `snapshot.env`   `PYTHON_DIGEST`       | manual bump | per Python patch |
| `pins.env`                             | Renovate PR | weekly |
| `requirements/<variant>.txt`           | pip-compile-multi PR | weekly |

## Adding a CLI to a variant

1. Add the binary's version + per-arch SHA-256 to `pins.env`.
2. Add a download-and-verify block to that variant's Dockerfile (copy the pattern from `base/Dockerfile`'s `gh` step).
3. Run the PR sanity build via `.github/workflows/ci-sandbox-images.yml` (triggers automatically on any `images/**` change).
4. Local smoke: `sagewai sandbox validate ghcr.io/sagewai/sandbox-<variant>:dev` after a local `docker buildx build --load`.
