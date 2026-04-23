# sagewai-sandbox-base

Minimal image for the Sagewai tool runner. Development tag `:dev`.

## Build locally

From repo root:

    docker build \
        -f packages/tool-runner/images/base/Dockerfile \
        -t ghcr.io/sagewai/sandbox-base:dev \
        .

## Smoke test

    # Start an idle container
    CID=$(docker run -d --rm ghcr.io/sagewai/sandbox-base:dev)

    # Execute a single tool-runner pass via docker exec
    echo '{"jsonrpc":"2.0","method":"exec","params":{"tool":"bash","args":{"command":"uname -a"},"call_id":"c1","timeout_s":5},"id":1}' \
        | docker exec -i "$CID" sagewai-tool-runner

    docker stop "$CID"

Expected: a single JSON line with `result.ok=true` and the kernel info in stdout.

## Plan 2

Plan 2 replaces this Dockerfile with a SHA-pinned multi-stage build, SBOM,
digest-pinned release manifest, and GHCR publish workflow.
