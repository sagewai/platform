# Backup & restore — operator runbook

**Status:** authoritative
**Last revised:** 2026-06-11

Covers backing up and restoring a Sagewai admin deployment in both run modes:
**multi-tenant** (Postgres) and **single-org** (a JSON state file). The single
most important rule, in either mode:

> **The master key is NOT in the database (or the state file). Back it up
> separately and securely. If you lose the master key, every encrypted secret
> — provider credentials, connector secrets, notification secrets — is
> permanently unrecoverable.** A database backup without the key restores the
> ciphertext but cannot decrypt it.

The master key resolves (in order) from `SAGEWAI_MASTER_KEY` → OS keychain →
`$SAGEWAI_HOME/secrets/master.key`. Whichever source you use, that secret must
have its own independent, access-controlled backup (a secrets manager / sealed
vault / offline copy) — never co-located with the data backup.

---

## A. Multi-tenant (Postgres)

In multi-tenant mode all tenant data lives in one Postgres database. Per-project
**data keys** are wrapped (encrypted) under the org master key and stored in the
database (`project.data_key_ref`); the org `master_key_ref` is a hint, not the
key itself. Rotating the master key re-wraps the data keys — it does not change
the data keys — so a backup taken under one master key still restores under the
same key.

### What to back up

A single logical Postgres database holds all of the following (back up the whole
database — these are listed so you can verify a restore covers them):

| Concern | Tables |
| --- | --- |
| Identity / tenancy | `org`, `user_account`, `project`, `membership`, `invitation`, `user_session` |
| Per-project key wrapping | `project.data_key_ref` (column on `project`), `org.master_key_ref` |
| Encrypted resource secrets | `provider`, `connection`, `admin_resources` (secret fields encrypted at rest under the project/org data key) |
| Audit chains (hash-linked) | `audit_event`, `audit_chain_head` |
| API tokens | `api_token` (hashes only — never plaintext) |
| Runs / prompt logs | `agent_runs`, `prompt_logs`, `workflow_runs` |
| Cost / budget / guardrails | `cost_records`, `budget_limits`, `budget_spend`, `guardrail_configs`, `guardrail_events` |

> The encrypted secret columns are useless without the **master key** (see the
> warning above). Back the key up separately.

### Backup (logical dump)

```bash
# Whole database, custom format (compressed, allows selective + parallel restore).
pg_dump \
  --format=custom \
  --no-owner --no-privileges \
  --file=sagewai-$(date +%Y%m%d-%H%M%S).dump \
  "$SAGEWAI_DATABASE_URL"
```

Take backups while the app is running (a logical dump is transaction-consistent).
For point-in-time recovery, use base backups + WAL archiving (`pg_basebackup` +
`archive_mode`) — out of scope here, but compatible with the encryption model
(it is all ciphertext at rest).

### Restore

```bash
# 1. Create / select the target database, then bring the schema to head FIRST
#    (see section C). On a fresh database this creates every table; on an
#    existing one it is a no-op.
SAGEWAI_DATABASE_URL="$TARGET_URL" sagewai db upgrade

# 2. Restore the data. --clean drops existing objects first; omit it when
#    restoring into a freshly migrated, empty database.
pg_restore \
  --no-owner --no-privileges \
  --dbname="$TARGET_URL" \
  --jobs=4 \
  sagewai-YYYYMMDD-HHMMSS.dump

# 3. Make the SAME master key available to the restored deployment
#    (env var, keychain, or $SAGEWAI_HOME/secrets/master.key). Without it the
#    app starts but cannot decrypt any project's secrets.
export SAGEWAI_MASTER_KEY='<the key that was in force when the dump was taken>'
```

### Restore-drill checklist

Run this end-to-end against a scratch database at least quarterly — a backup you
have never restored is a hypothesis, not a backup.

- [ ] Provision a throwaway Postgres database; set `TARGET_URL`.
- [ ] `sagewai db upgrade` succeeds (schema is at head; section C).
- [ ] `pg_restore` completes with no errors.
- [ ] Make the **correct** master key available (the one in force at dump time).
- [ ] Start admin with `SAGEWAI_ENV=production` (or staging) — the production
      validator (`SAGEWAI_TENANCY_MODE`, Postgres `DATABASE_URL`, master key,
      host-exec off, explicit CORS, TLS) must pass.
- [ ] `GET /api/v1/organization` and `GET /api/v1/projects` return the expected
      tenants.
- [ ] Open a provider/connection with a secret and confirm a **test** succeeds
      (proves the secret decrypted — i.e. the right master key restored).
- [ ] Verify an audit chain: read a project's chain and confirm it verifies
      (hash-linked, unbroken) — see `GET /api/v1/audit/...`.
- [ ] Confirm API tokens are present but list as **redacted** (hash only, never
      plaintext); old plaintext tokens are not recoverable by design — reissue
      if needed.

---

## B. Single-org

Single-org persists admin state to one JSON file, not Postgres.

### What to back up

| Item | Default path | Override |
| --- | --- | --- |
| Admin state (org, admin, providers, tokens, audit events, connectors) | `$SAGEWAI_HOME/config/admin-state.json` (i.e. `~/.sagewai/config/admin-state.json`) | `SAGEWAI_ADMIN_STATE_FILE` |
| Master key (encrypts provider secrets in the state file) | `$SAGEWAI_HOME/secrets/master.key` | `SAGEWAI_MASTER_KEY` env / OS keychain |

> Provider secret fields inside `admin-state.json` are encrypted under the
> master key. Same rule as multi-tenant: **back up the key separately.** A copy
> of `admin-state.json` alone cannot decrypt its own secrets.

If a Postgres `SAGEWAI_DATABASE_URL` is also configured in single-org (some
durable surfaces — runs, prompt logs, harness, vector memory — can persist
there), back that database up as in section A as well.

### Backup

```bash
# Copy both the state file AND the key (to independent, access-controlled stores).
cp ~/.sagewai/config/admin-state.json  /backup/admin-state-$(date +%Y%m%d).json
cp ~/.sagewai/secrets/master.key       /secure-keystore/master.key   # separate location!
```

### Restore

```bash
# 1. Put the state file back.
cp /backup/admin-state-YYYYMMDD.json  ~/.sagewai/config/admin-state.json

# 2. Restore the SAME master key (file at 0600, or env var, or keychain).
cp /secure-keystore/master.key  ~/.sagewai/secrets/master.key
chmod 0600 ~/.sagewai/secrets/master.key

# 3. Start admin; confirm a provider test decrypts (proves the key matches).
```

The key file must be `0600` — the resolver refuses to read a key file with
group/other permissions.

---

## C. Migrate before you restore (Postgres only)

Always bring the **schema** to head before loading **data**, so the target
database has every table/column the dump expects. Sagewai uses Alembic; the
migration environment reads `SAGEWAI_DATABASE_URL`.

```bash
# `sagewai db upgrade` runs the bundled Alembic migrations
# (packages/sdk/sagewai/db/migrations) — it resolves them itself, so you can
# run it from anywhere. There is no alembic.ini; the CLI is the entrypoint.
SAGEWAI_DATABASE_URL="$TARGET_URL" sagewai db upgrade

# Verify which revision a database is on:
psql "$TARGET_URL" -c "SELECT version_num FROM alembic_version;"
```

Notes:

- On **SQLite** (the zero-config default), the app bootstraps the schema itself
  on startup — Alembic is the production/Postgres path.
- Restore order is **schema (migrate) → data (`pg_restore`) → key**. Loading a
  dump into an un-migrated database, or into one at a *newer* revision than the
  dump, can fail on missing/extra columns. Match the deployed app version to the
  backup's schema revision; if they differ, migrate to head and let any
  data-migration steps run before serving traffic.
- A schema-only restore (`pg_restore --schema-only`) is a fast way to validate
  the dump against the current models before a full restore drill.
