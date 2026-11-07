# Infrastructure and delivery standards

Use these defaults for new projects. Existing infrastructure stays on its working
engine until a separate migration has a reason, a state backup, and a no-change
plan. Keep project-specific deployment commands and approvals in the repo.

## IaC decision

| Choice | Use it when | Decision here |
| --- | --- | --- |
| OpenTofu (HCL) | New cloud resources, small to medium stacks, open-source default | Default for new IaC |
| Terraform (HCL) | Existing stack or provider/module compatibility requires it | Keep existing stacks; do not migrate just for uniformity |
| Terragrunt | Several repeated environments or many dependent states create real duplication | Optional wrapper around OpenTofu or Terraform, not a replacement |
| Pulumi | Resource composition needs ordinary language features and a team owns the extra runtime, SDK, and state workflow | Explicit exception, not the default |

OpenTofu and Terraform share the HCL model, but compatibility is not a promise for
every future provider, module, or state feature. Test the exact pinned versions.
Pulumi is mature and supports TypeScript, Python, Go, and self-managed or hosted
state; use it where that programming model pays for its operational cost.

Keep local Mac/Linux service setup in versioned `just` recipes and deployment
scripts; use IaC for cloud API resources that need repeatable change control.
A handful of resources rarely needs Terragrunt. Existing Terraform stacks stay
on Terraform until there is a concrete migration benefit.

## State and resource ownership

- One IaC owner per resource. Import before taking over existing resources; never
  let two engines or states manage the same object. Separate prod from non-prod
  state and credentials, and keep state out of Git.
- Use encrypted, versioned remote state with access controls and verified locking.
  Back up state before engine/backend changes. Do not assume an S3-compatible
  object store offers safe locking; verify its conditional-write behavior first.
- Pin the IaC CLI, providers, modules, and lockfile. Review planned create,
  update, replace, and destroy actions. Require a human decision for deletion or
  replacement of persistent data, public access changes, or irreversible moves.
- Put secrets in a secret manager or short-lived identity, not variables files,
  plans, logs, or outputs. Plan files and state can contain sensitive values.
- Keep modules small and owned by a service or platform boundary. Use one module
  only after the second real use; avoid a generic module catalog in a small repo.
- Detect drift on a schedule and report it. Reconcile through code or a documented
  import; do not auto-apply drift corrections to production.

## Fast CI

- Keep one required change gate: formatting, lint/type checks, build, fast
  deterministic unit tests for affected packages and dependents, plus generated
  file and lockfile checks. If impact detection cannot be trusted, run the full
  unit suite. Cancel superseded PR runs; keep release/deploy runs serialized.
- On an IaC change, run format and validation. On trusted branches, run a
  read-only plan against the intended environment with a restricted identity and
  attach a concise summary. Untrusted forks get no cloud credentials; never
  apply from a PR.
- Run integration, browser, race/concurrency, security, and full-suite checks
  only where the changed boundary needs them; schedule the broader sweep and run
  it before releases. A flaky check is a failing check: fix or remove it, no CI
  retry policy. Track gate duration and remove redundant checks.
- Pin GitHub Actions by full commit SHA and dependencies by exact versions.
  Minimize `GITHUB_TOKEN` permissions; use OIDC for cloud roles where supported,
  otherwise narrow rotating tokens. Keep untrusted PR code away from credentials.

## CD and rollback

- Build once from a reviewed commit, publish an immutable image or artifact by
  digest, and promote that same artifact between environments. Keep app deploys
  separate from IaC applies; routine application releases should not re-provision
  infrastructure.
- Deploy from protected main or release refs with a per-environment concurrency
  group. Use environment protection for production, especially when persistent
  resources change. Record commit, artifact digest, environment, operator or
  workflow, and health result.
- After deployment, check a small set of user-visible health signals and stop or
  roll back on a failed release. Keep the previous artifact deployable; make
  database migrations forward-compatible and use a restore runbook for data loss.
  Rehearse rollback and restore periodically.
- On the deployment commit, re-plan with an environment-scoped identity and
  state locking, review any difference from the change plan, then apply that exact
  saved plan. Secure and expire plan artifacts because they may contain secrets.
  Production destroy or state surgery needs explicit review.

## Developer commands and hooks

- Use `just` as the human and CI command menu: `setup`, `fmt`, `check`, `test`,
  `build`, `plan`, `deploy`, and `rollback` where relevant. Recipes call the same
  versioned scripts or native tools in CI and locally. `just` is a command runner;
  keep dependency graphs in language-native build tools.
- Keep a `Makefile` only when a tool or existing ecosystem expects `make`.
  Delegate to `just` or the same scripts; never maintain two implementations of
  a check. Preserve existing repository command names during adoption.
- Pre-commit hooks run only staged-file formatting, whitespace checks, and a
  fast secret check. Target under five seconds. No network, service startup,
  full test suite, or auto-generated surprise edits in a commit hook. CI remains
  authoritative because hooks can be skipped.
- Prefer the repository's existing hook mechanism. For a mixed-language repo
  without one, Lefthook is a reasonable optional runner for staged-file hooks;
  do not add a hook framework to a small single-language project just for this.

## Primary references

- [OpenTofu migration and compatibility](https://opentofu.org/docs/intro/migration/)
- [Terraform state and locking](https://developer.hashicorp.com/terraform/language/state)
- [Terragrunt overview](https://docs.terragrunt.com/getting-started/overview/)
- [Pulumi backends](https://www.pulumi.com/docs/iac/concepts/state-and-backends/)
- [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [just manual](https://just.systems/man/en/) and [Lefthook](https://github.com/evilmartians/lefthook)
