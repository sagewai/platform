# Go project guidelines for AI development and fast CI

Copy this file into another Go repository and link to it from that repository's
`AGENTS.md`. Keep repository-specific commands, boundaries, and exceptions in that
repository's own instructions. Its local rules take precedence.

## Code and dependencies

- Keep packages cohesive and dependencies explicit. Prefer the standard library;
  add a library when it removes meaningful work, and record why it is needed.
- Pass `context.Context` first across request boundaries. Return errors with useful
  context and `%w`; keep ownership of goroutines, cancellation, and shared state clear.
- Define narrow interfaces at the consuming boundary when multiple implementations
  or a useful test seam exists. Avoid global mutable state and `init` side effects.
- Use supported Go releases. Pin the module's Go version and tool dependencies;
  review module upgrades deliberately. Format with `gofmt` and keep generated files
  reproducible.

## Configuration

- Define typed configuration and an explicit precedence order: defaults, config
  files, environment, then explicitly supplied flags. Validate once before startup.
- Inject validated config through constructors. Avoid package-level configuration
  lookups inside business logic. Keep secrets out of committed config and logs.
- For runtime changes, publish immutable, versioned snapshots; consumers read one
  snapshot per operation. Resolve tenant config at the boundary, with documented
  defaults and overrides, and isolate each tenant's values. Apply changes atomically
  and retain the previous valid snapshot when validation fails.
- Koanf is a reasonable choice when multiple config sources are needed. Use only
  the providers the project actually needs; keep the typed schema and validation
  independent of the loader.

## AI development

- Give each task a concrete goal, acceptance claims, file scope, and the commands
  that prove it. Make small, complete changes and review the diff against the
  claims and repository rules before merging.
- Ask for an independent review where the project workflow requires one. Record
  unresolved findings and keep generated changes traceable to their source.

## Tests

- Test observable behavior at the lowest level that proves a claim. Use named,
  table-driven cases with `t.Run` for variants of the same behavior; include edge
  cases that can actually fail. Avoid tests of getters, constructor field copies,
  mock calls, or implementation details solely to raise coverage.
- Make tests deterministic: use `t.TempDir`, `httptest`, controlled clocks, explicit
  synchronization, and local fakes for external boundaries. No sleeps, live service
  dependencies, shared mutable fixtures, ordering assumptions, retries, or timing
  assertions in the required suite. Fix or remove a flaky test before merging.
- Add integration or end-to-end tests only for contracts that unit tests cannot
  observe, and keep the smallest set of journeys that covers those contracts.

## CI

- Keep the required change gate short: formatting, `go vet`, build, module
  tidiness, generated-file drift, and focused tests for affected packages and their
  dependents. If package selection is unreliable, run the full unit suite.
- Run race checks for changed concurrent code and relevant integration tests when
  their boundaries change. Run the complete suite and broader security checks,
  including `govulncheck`, on a scheduled or release gate; check dependencies when
  they change. Never hide failures with retries or unconditional skips.
- Prefer a small set of stable, meaningful checks over a coverage percentage or a
  large collection of tests that repeat the implementation.
