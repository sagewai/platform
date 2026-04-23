# sagewai-tool-runner

The in-sandbox tool runner for Sagewai. Speaks JSON-RPC 2.0 over stdin/stdout.

Ships inside every `sagewai/sandbox-*` image. Each tool call is a fresh
`docker exec sagewai-tool-runner` against a long-running sandbox container.

See the repo design spec at
`docs/superpowers/specs/2026-04-20-agent-sandboxing-design.md` for context.
