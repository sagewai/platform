from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class EvidenceRequired(ValueError):
    pass


class AlreadyDecided(ValueError):
    pass


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    project_id: str
    title: str
    risk: str
    status: str
    created_at: str
    decided_at: str | None
    decided_by: str | None
    evidence_ref: str | None
    decision_reason: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class ApprovalDesk:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    evidence_ref TEXT,
                    decision_reason TEXT,
                    PRIMARY KEY (project_id, id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_request_scope
                    ON events (project_id, request_id, sequence);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _request(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(**dict(row))

    @staticmethod
    def _require_text(value: str, field: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field} is required")
        return cleaned

    def create_request(
        self, *, project_id: str, title: str, risk: str
    ) -> ApprovalRequest:
        project_id = self._require_text(project_id, "project_id")
        title = self._require_text(title, "title")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("risk must be low, medium, high, or critical")
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            project_id=project_id,
            title=title,
            risk=risk,
            status="pending",
            created_at=self._now(),
            decided_at=None,
            decided_by=None,
            evidence_ref=None,
            decision_reason=None,
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO requests (
                    id, project_id, title, risk, status, created_at,
                    decided_at, decided_by, evidence_ref, decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.id,
                    request.project_id,
                    request.title,
                    request.risk,
                    request.status,
                    request.created_at,
                    request.decided_at,
                    request.decided_by,
                    request.evidence_ref,
                    request.decision_reason,
                ),
            )
            self._append_event(connection, request, "created", {"risk": request.risk})
        return request

    def get(self, project_id: str, request_id: str) -> ApprovalRequest:
        project_id = self._require_text(project_id, "project_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM requests WHERE project_id = ? AND id = ?",
                (project_id, request_id),
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return self._request(row)

    def pending(self, *, project_id: str) -> list[ApprovalRequest]:
        project_id = self._require_text(project_id, "project_id")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM requests
                WHERE project_id = ? AND status = 'pending'
                ORDER BY created_at, id
                """,
                (project_id,),
            ).fetchall()
        return [self._request(row) for row in rows]

    def decide(
        self,
        *,
        project_id: str,
        request_id: str,
        decision: str,
        actor: str,
        reason: str,
        evidence_ref: str | None = None,
    ) -> ApprovalRequest:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        project_id = self._require_text(project_id, "project_id")
        actor = self._require_text(actor, "actor")
        reason = self._require_text(reason, "reason")
        evidence_ref = (evidence_ref.strip() or None) if evidence_ref else None

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM requests WHERE project_id = ? AND id = ?",
                (project_id, request_id),
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            current = self._request(row)
            if current.status != "pending":
                raise AlreadyDecided(
                    f"request {request_id} is already {current.status}"
                )
            if (
                decision == "approved"
                and current.risk in {"high", "critical"}
                and not evidence_ref
            ):
                raise EvidenceRequired(
                    f"{current.risk} risk approval requires evidence"
                )

            decided_at = self._now()
            result = connection.execute(
                """
                UPDATE requests
                SET status = ?, decided_at = ?, decided_by = ?,
                    evidence_ref = ?, decision_reason = ?
                WHERE project_id = ? AND id = ? AND status = 'pending'
                """,
                (
                    decision,
                    decided_at,
                    actor,
                    evidence_ref,
                    reason,
                    project_id,
                    request_id,
                ),
            )
            if result.rowcount != 1:
                raise AlreadyDecided(f"request {request_id} is no longer pending")
            decided = ApprovalRequest(
                **{
                    **current.to_dict(),
                    "status": decision,
                    "decided_at": decided_at,
                    "decided_by": actor,
                    "evidence_ref": evidence_ref,
                    "decision_reason": reason,
                }
            )
            self._append_event(
                connection,
                decided,
                decision,
                {
                    "actor": actor,
                    "reason": reason,
                    "evidence_ref": evidence_ref,
                },
            )
        return decided

    def history(self, *, project_id: str, request_id: str) -> list[dict]:
        project_id = self._require_text(project_id, "project_id")
        self.get(project_id, request_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence, kind, payload_json, created_at
                FROM events
                WHERE project_id = ? AND request_id = ?
                ORDER BY sequence
                """,
                (project_id, request_id),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _append_event(
        self,
        connection: sqlite3.Connection,
        request: ApprovalRequest,
        kind: str,
        payload: dict,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events (request_id, project_id, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                request.id,
                request.project_id,
                kind,
                json.dumps(payload, sort_keys=True),
                self._now(),
            ),
        )
