# syntax=docker/dockerfile:1.7

# sagewai-sandbox-base — hardened base for every Sagewai sandbox variant.
#
# Reproducibility:
#   - python:3.12-slim-bookworm pinned by digest (snapshot.env)
#   - apt pulls from snapshot.debian.org at a frozen date (snapshot.env)
#   - pip uses --require-hashes against the committed lockfile
#   - binary CLIs verified by SHA-256 (pins.env)

ARG SNAPSHOT_DATE
ARG PYTHON_DIGEST
ARG TOOL_RUNNER_VERSION=0.1.0

FROM python:3.12-slim-bookworm@${PYTHON_DIGEST}

ARG SNAPSHOT_DATE
ARG TOOL_RUNNER_VERSION
ARG TARGETARCH
ARG GH_VERSION=2.63.2
ARG GH_AMD64_SHA256
ARG GH_ARM64_SHA256
ARG YQ_VERSION=4.44.3
ARG YQ_AMD64_SHA256
ARG YQ_ARM64_SHA256

# ── apt from frozen snapshot ────────────────────────────────────────
RUN echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${SNAPSHOT_DATE}/ bookworm main" \
      > /etc/apt/sources.list \
  && echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/${SNAPSHOT_DATE}/ bookworm-security main" \
      >> /etc/apt/sources.list \
  && apt-get -o Acquire::Check-Valid-Until=false update \
  && apt-get install -y --no-install-recommends \
       git \
       curl \
       jq \
       ripgrep \
       ca-certificates \
       tini \
  && rm -rf /var/lib/apt/lists/*

# yq isn't in Debian's apt; install as a Go binary.
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) YQ_SHA="${YQ_AMD64_SHA256}"; YQ_ARCH="amd64" ;; \
      arm64) YQ_SHA="${YQ_ARM64_SHA256}"; YQ_ARCH="arm64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/yq "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_${YQ_ARCH}"; \
    echo "${YQ_SHA}  /tmp/yq" | sha256sum -c; \
    install -m 0755 /tmp/yq /usr/local/bin/yq; \
    rm /tmp/yq

# ── gh CLI ──────────────────────────────────────────────────────────
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) GH_SHA="${GH_AMD64_SHA256}"; GH_ARCH="amd64" ;; \
      arm64) GH_SHA="${GH_ARM64_SHA256}"; GH_ARCH="arm64" ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/gh.tar.gz \
       "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${GH_ARCH}.tar.gz"; \
    echo "${GH_SHA}  /tmp/gh.tar.gz" | sha256sum -c; \
    tar -xzf /tmp/gh.tar.gz -C /usr/local --strip-components=1; \
    rm /tmp/gh.tar.gz

# ── pip: hash-pinned transitive deps ────────────────────────────────
COPY packages/tool-runner/images/requirements/base.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
  && rm /tmp/requirements.txt

# ── sagewai-tool-runner (from monorepo source for :dev; from PyPI for release) ──
# Note: base.txt does NOT include sagewai-tool-runner itself because it is not yet
# published to PyPI. The release-sandbox.yml workflow appends the PyPI pin to base.in
# and regenerates base.txt before building versioned images. For local :dev builds
# the package is installed from the monorepo source below.
COPY packages/tool-runner /tmp/tool-runner
RUN pip install --no-cache-dir /tmp/tool-runner \
  && rm -rf /tmp/tool-runner

WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
