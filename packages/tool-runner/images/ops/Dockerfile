# syntax=docker/dockerfile:1.7

# sagewai-sandbox-ops — extends general with infra/DevOps tooling.
#
# Reproducibility:
#   - Inherits all general guarantees (snapshot apt, pinned Python, hash-pinned pip)
#   - kubectl: bare binary, SHA-256-verified (official K8s release server)
#   - helm: tar.gz extract, SHA-256-verified (official Helm release server)
#   - terraform: zip extract, SHA-256-verified (Hashicorp release server)
#   - docker CLI: tgz extract, SHA-256-verified (Docker static builds)
#   - pip extras: kubernetes, hcloud, python-terraform (hash-pinned via ops.txt)
#   - openssh-client: from frozen Debian snapshot (inherited apt sources)

# For :dev local builds pass SANDBOX_GENERAL_TAG=dev (no registry digest needed).
# For CI/release builds pass SANDBOX_GENERAL_DIGEST=sha256:... instead.
ARG SANDBOX_GENERAL_TAG=dev
ARG SANDBOX_GENERAL_DIGEST
# hadolint ignore=DL3006
FROM ghcr.io/sagewai/sandbox-general:${SANDBOX_GENERAL_TAG}

ARG TARGETARCH

# ── kubectl pins ──────────────────────────────────────────────────
ARG KUBECTL_VERSION
ARG KUBECTL_AMD64_SHA256
ARG KUBECTL_ARM64_SHA256

# ── helm pins ─────────────────────────────────────────────────────
ARG HELM_VERSION
ARG HELM_AMD64_SHA256
ARG HELM_ARM64_SHA256

# ── terraform pins ────────────────────────────────────────────────
ARG TERRAFORM_VERSION
ARG TERRAFORM_AMD64_SHA256
ARG TERRAFORM_ARM64_SHA256

# ── docker CLI pins ───────────────────────────────────────────────
ARG DOCKER_CLI_VERSION
ARG DOCKER_CLI_AMD64_SHA256
ARG DOCKER_CLI_ARM64_SHA256

USER root

# ── 1. System packages: unzip (terraform) + openssh-client ───────
RUN apt-get -o Acquire::Check-Valid-Until=false update \
  && apt-get install -y --no-install-recommends \
       openssh-client \
       unzip \
  && rm -rf /var/lib/apt/lists/*

# ── 2. kubectl ────────────────────────────────────────────────────
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${KUBECTL_AMD64_SHA256}" ;; \
      arm64) SHA="${KUBECTL_ARM64_SHA256}" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/kubectl \
        "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl"; \
    echo "${SHA}  /tmp/kubectl" | sha256sum -c; \
    install -m 0755 /tmp/kubectl /usr/local/bin/kubectl; \
    rm /tmp/kubectl

# ── 3. helm ───────────────────────────────────────────────────────
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${HELM_AMD64_SHA256}" ;; \
      arm64) SHA="${HELM_ARM64_SHA256}" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/helm.tar.gz \
        "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    echo "${SHA}  /tmp/helm.tar.gz" | sha256sum -c; \
    tar -xzf /tmp/helm.tar.gz -C /tmp; \
    install -m 0755 "/tmp/linux-${TARGETARCH}/helm" /usr/local/bin/helm; \
    rm -rf /tmp/helm.tar.gz "/tmp/linux-${TARGETARCH}"

# ── 4. terraform ──────────────────────────────────────────────────
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${TERRAFORM_AMD64_SHA256}" ;; \
      arm64) SHA="${TERRAFORM_ARM64_SHA256}" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/terraform.zip \
        "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip"; \
    echo "${SHA}  /tmp/terraform.zip" | sha256sum -c; \
    unzip -q /tmp/terraform.zip -d /usr/local/bin; \
    chmod +x /usr/local/bin/terraform; \
    rm /tmp/terraform.zip

# ── 5. docker CLI (client only — no daemon) ───────────────────────
# Docker's static builds use x86_64 / aarch64 naming (not amd64 / arm64)
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SHA="${DOCKER_CLI_AMD64_SHA256}"; ARCH="x86_64" ;; \
      arm64) SHA="${DOCKER_CLI_ARM64_SHA256}"; ARCH="aarch64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/docker.tgz \
        "https://download.docker.com/linux/static/stable/${ARCH}/docker-${DOCKER_CLI_VERSION}.tgz"; \
    echo "${SHA}  /tmp/docker.tgz" | sha256sum -c; \
    tar -xzf /tmp/docker.tgz -C /tmp; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    rm -rf /tmp/docker.tgz /tmp/docker

# ── 6. pip extras (kubernetes, hcloud, python-terraform) ─────────
COPY packages/tool-runner/images/requirements/ops.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt

WORKDIR /workspace
