# Sealed-iii.B Redaction — operator runbook

**Status:** authoritative
**Last revised:** 2026-04-28

## What this enforces

Every Tier-2 secret value injected into a sandbox is scrubbed out of
`ToolResult.stdout` / `stderr` / `error` before the host persists or
streams the result. Audit events `redaction.match` fire once per
(secret_key, surface) tuple per `ToolResult`. A high rate of matches
indicates a misbehaving CLI agent leaking values via stdout.

## Tuning knobs

`RedactionConfig` defaults (in `sagewai.sealed.redaction`):

- `placeholder_template = "<redacted:{name}>"`
- `min_value_length = 8` — values shorter than this are not redacted.
- `max_input_bytes = 8 MiB` — oversize inputs bypass redaction with a
  loud `redaction.skipped_oversize` audit. The bypass is a v1
  trade-off; tune via the pool's redactor config if your CLI generates
  larger blobs.

## Alert thresholds

Suggested Grafana panel (under the existing "Sagewai Sealed" row):

```
count_over_time(
  {sagewai_event="sagewai.sealed.redaction.match"}[$__interval]
)
```

- **Warning** at >10 matches/min for any one (profile, secret_key,
  tool) tuple — likely a sloppy CLI agent.
- **Critical** at >100 matches/min — possible exfiltration attempt;
  consider hard-revoke of the affected key (`sagewai admin sealed
  revoke --hard <profile> <key>`).

## Common false positives

- Behavior knob mistakenly tagged secret (e.g., `DEBUG=1` in the
  `secrets` block): the `min_value_length=8` default skips it, but
  if someone sets a 9-char common string as a secret, unrelated
  output containing that substring will be redacted. Move behavior
  knobs to the `env` block of the profile.
- A short version string set as a secret (`v1.2.3`): same as above.

## When to disable redaction

Don't. The opt-out doesn't exist by design. If a specific operator
truly needs raw output (rare — typically debug-only), they can
construct a `Redactor` with an empty dict at the consumer site and
operate outside the wrapper.
