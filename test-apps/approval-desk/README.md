# Approval Desk

A dependency-free Python application for experimenting with safe automation of
middleman work. It persists project-scoped requests in SQLite, requires evidence
for high-risk approvals, makes decisions immutable, and retains an audit history.
It is a test target for Sagewai, not a second control plane.

## Run it

```bash
cd test-apps/approval-desk
python3 app.py --db demo.db create \
  --project operations \
  --title "Approve supplier quote" \
  --risk medium

python3 app.py --db demo.db pending --project operations
```

Copy the returned request ID and decide it:

```bash
python3 app.py --db demo.db decide REQUEST_ID \
  --project operations \
  --decision approved \
  --actor arda \
  --reason "Quote matches the signed contract"

python3 app.py --db demo.db history REQUEST_ID --project operations
```

High- and critical-risk approvals additionally require `--evidence-ref`. A
rejection never requires evidence because it does not authorize the requested
action.

## Verification contract

```bash
just smoke
```

The suite uses only Python's standard library and exercises persistence,
project isolation, risk gates, immutable decisions, audit history, and the real
CLI round trip.

## Suggested Sagewai work

```text
In test-apps/approval-desk, add an expires_at field. Expired pending requests
must be shown separately and cannot be approved. Preserve project isolation and
immutable history, add restart-safe tests, and change nothing outside this app.
```

```text
In test-apps/approval-desk, add a read-only summary command that groups pending
requests by risk. It must never expose another project and must not change any
request or event. Add CLI and persistence tests without adding dependencies.
```
