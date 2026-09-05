#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
verifier_tag="${1:-sagewai-verifier:dev}"
cd "${repo_root}"

set -a
source packages/tool-runner/images/snapshot.env
source packages/tool-runner/images/pins.env
set +a

docker buildx build \
  --file packages/tool-runner/images/base/Dockerfile \
  --build-arg SNAPSHOT_DATE \
  --build-arg PYTHON_DIGEST \
  --build-arg GH_VERSION \
  --build-arg GH_AMD64_SHA256 \
  --build-arg GH_ARM64_SHA256 \
  --build-arg YQ_VERSION \
  --build-arg YQ_AMD64_SHA256 \
  --build-arg YQ_ARM64_SHA256 \
  --tag ghcr.io/sagewai/sandbox-base:dev \
  --load \
  .

docker buildx build \
  --file scripts/docker/work-verifier.Dockerfile \
  --tag "${verifier_tag}" \
  --load \
  .

docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --user "$(id -u):$(id -g)" \
  --volume "${repo_root}:/workspace:ro" \
  --entrypoint just \
  "${verifier_tag}" smoke

image_id="$(
  docker image inspect "${verifier_tag}" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["Id"])'
)"
# Docker prints sha256:<hex>; Podman prints the bare hex. Sagewai accepts the digest form only.
printf '\nVerifier ready. Use this exact immutable local image digest:\n'
printf 'export SAGEWAI_WORK_VERIFICATION_IMAGE=%s\n' "sha256:${image_id#sha256:}"
