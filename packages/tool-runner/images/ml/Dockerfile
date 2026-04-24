# syntax=docker/dockerfile:1.7

# sagewai-sandbox-ml — extends general with ML/data-science tooling (CPU only).
#
# Reproducibility:
#   - Inherits all general guarantees (snapshot apt, pinned Python, hash-pinned pip)
#   - All pip packages pinned by version + SHA-256 via --require-hashes
#   - No binary CLIs beyond what general provides
#   - torch: CPU-only wheel (torch==2.10.0+cpu) from PyTorch's CPU index
#     Extra index URL is required at install time because the +cpu variant is
#     not published to PyPI; it lives at https://download.pytorch.org/whl/cpu

# For :dev local builds pass SANDBOX_GENERAL_TAG=dev (no registry digest needed).
# For CI/release builds pass SANDBOX_GENERAL_DIGEST=sha256:... instead.
ARG SANDBOX_GENERAL_TAG=dev
ARG SANDBOX_GENERAL_DIGEST
# hadolint ignore=DL3006
FROM ghcr.io/sagewai/sandbox-general:${SANDBOX_GENERAL_TAG}

USER root

COPY packages/tool-runner/images/requirements/ml.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt

WORKDIR /workspace
