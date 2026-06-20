# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLAlchemy-backed durable fleet task store (B1).

Mirrors :class:`sagewai.fleet.registry.PostgresFleetRegistry`: factory engine,
idempotent fail-closed ``init()``, lazy ``_ensure_init``/``_begin``/``_connect``,
atomic CAS writes. Implements the :class:`sagewai.fleet.dispatcher.TaskStore`
Protocol plus the ``get_task``/``list_tasks`` status reads.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sagewai.fleet.dispatcher import _TERMINAL_STATUSES, NotTaskOwnerError
from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


class PostgresTaskStore:
    def __init__(self, *, engine=None, lease_ttl_seconds: float = 60.0, max_attempts: int = 3) -> None:
        from sagewai.db import factory
        from sagewai.db.models import FleetTaskModel

        self._engine = engine or factory.get_engine()
        self._tasks = FleetTaskModel.__table__
        self._inited = False
        self._lease_ttl_seconds = lease_ttl_seconds
        self._max_attempts = max_attempts

    async def init(self) -> None:
        if self._inited:
            return
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError, ProgrammingError

        from sagewai.db.models import Base

        eng = self._engine
        async with eng.begin() as conn:
            if eng.dialect.name == "sqlite":
                # Build new tables, then upgrade an older home in place (add any model
                # columns + indexes an existing fleet_tasks lacks) so the probe passes.
                from sagewai.db.factory import add_missing_sqlite_columns

                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(add_missing_sqlite_columns)
            # Fail-closed on BOTH dialects: a fleet_tasks missing lease_expires_at/attempts
            # raises HERE, not at first claim/renew/reap.
            try:
                await conn.execute(
                    text("SELECT run_id, status, lease_expires_at, attempts FROM fleet_tasks LIMIT 0")
                )
            except (OperationalError, ProgrammingError) as exc:
                raise RuntimeError(self._unmigrated_hint(eng.dialect.name)) from exc
        self._inited = True

    @staticmethod
    def _unmigrated_hint(dialect: str) -> str:
        if dialect == "postgresql":
            return (
                "fleet_tasks is missing columns from a newer migration. Run "
                "`alembic upgrade head` against the database, then restart."
            )
        return (
            "fleet_tasks in the local SQLite home is from an older version and could not "
            "be auto-upgraded. Delete the fleet_tasks table (it's a transient queue — it "
            "will be rebuilt) and restart."
        )

    async def _ensure_init(self) -> None:
        if not self._inited:
            await self.init()

    @asynccontextmanager
    async def _begin(self):
        await self._ensure_init()
        async with self._engine.begin() as conn:
            yield conn

    @asynccontextmanager
    async def _connect(self):
        await self._ensure_init()
        async with self._engine.connect() as conn:
            yield conn

    def _now_expr(self):
        """The 'now' for lease comparisons. Postgres: the DB clock (func.now()) — the
        HA invariant (no app-process clock; see spec §1). SQLite: the app clock —
        correct (single-process, no skew) AND necessary (SQLite datetime() is only
        second-precision, too coarse for the sub-second leases the tests use)."""
        if self._engine.dialect.name == "postgresql":
            from sqlalchemy import func
            return func.now()
        return _now()

    def _lease_expr(self):
        """now + lease_ttl, on the SAME clock as _now_expr (see its docstring)."""
        if self._engine.dialect.name == "postgresql":
            from sqlalchemy import func
            # make_interval(years,months,weeks,days,hours,mins,secs) — unambiguous PG interval.
            return func.now() + func.make_interval(0, 0, 0, 0, 0, 0, self._lease_ttl_seconds)
        return _now() + timedelta(seconds=self._lease_ttl_seconds)

    # -- mapping --------------------------------------------------------
    @staticmethod
    def _claimed_view(row, worker_id, claimed_at) -> dict[str, Any]:
        """The task dict returned to a worker on claim (carries payload)."""
        m = row._mapping
        return {
            "run_id": m["run_id"], "org_id": m["org_id"], "project_id": m["project_id"],
            "pool": m["pool"], "model": m["model"], "labels": m["labels"] or {},
            "payload": m["payload"], "worker_id": worker_id,
            "claimed_at": claimed_at.isoformat(),
        }

    @staticmethod
    def _status_view(row) -> dict[str, Any]:
        """The metadata+result view returned by get_task/list_tasks (no payload)."""
        m = row._mapping
        return {
            "run_id": m["run_id"], "status": m["status"], "project_id": m["project_id"],
            "pool": m["pool"], "model": m["model"], "worker_id": m["worker_id"],
            "claimed_at": _iso(m["claimed_at"]), "error": m["error"], "output": m["output"],
            "reported_at": _iso(m["reported_at"]), "created_at": _iso(m["created_at"]),
        }

    # -- enqueue --------------------------------------------------------
    async def enqueue(self, task: dict[str, Any]) -> None:
        from sqlalchemy import insert

        t = self._tasks
        async with self._begin() as conn:
            await conn.execute(insert(t).values(
                run_id=task["run_id"],
                org_id=task["org_id"],
                project_id=task.get("project_id"),
                pool=task.get("pool", "default"),
                model=task.get("model"),
                labels=task.get("labels") or {},
                payload=task.get("payload") or {},
                status="pending",
            ))

    # -- status reads ---------------------------------------------------
    async def get_task(self, run_id, *, org_id, project_id) -> dict[str, Any] | None:
        from sqlalchemy import select

        t = self._tasks
        async with self._connect() as conn:
            row = (await conn.execute(select(t).where(
                (t.c.run_id == run_id) & (t.c.org_id == org_id)
                & (t.c.project_id == project_id)
            ))).first()
        return self._status_view(row) if row else None

    async def list_tasks(self, *, org_id, project_id, status=None, limit=50) -> list[dict[str, Any]]:
        from sqlalchemy import select

        t = self._tasks
        q = select(t).where((t.c.org_id == org_id) & (t.c.project_id == project_id))
        if status is not None:
            q = q.where(t.c.status == status)
        q = q.order_by(t.c.created_at.desc()).limit(limit)
        async with self._connect() as conn:
            rows = (await conn.execute(q)).all()
        return [self._status_view(r) for r in rows]

    async def claim_task(
        self, worker_id, org_id, models_canonical, pool, labels, *,
        project_id=None,
        worker_sandbox_mode: SandboxMode = SandboxMode.NONE,
        worker_sandbox_variants: list[SandboxImageVariant] | None = None,
        worker_network_policy: NetworkPolicy = NetworkPolicy.NONE,
    ) -> dict[str, Any] | None:
        from sqlalchemy import bindparam, or_, select, update

        t = self._tasks
        worker_labels = labels or {}
        base = (
            (t.c.status == "pending")
            & (t.c.org_id == org_id)        # exact org (durable store requires org)
            & (t.c.pool == pool)
            & (t.c.project_id == project_id)  # None -> IS NULL (org-global)
            & or_(t.c.model.is_(None), t.c.model.in_(models_canonical))
        )
        is_pg = self._engine.dialect.name == "postgresql"
        async with self._begin() as conn:
            if is_pg:
                from sqlalchemy.dialects.postgresql import JSONB

                # JSONB containment pushes the label-subset test into SQL, so the
                # single SKIP-LOCKED candidate is already a real match (no starvation).
                wl = bindparam("wl", worker_labels, type_=JSONB())
                sel = (
                    select(t).where(base & t.c.labels.op("<@")(wl))
                    .order_by(t.c.created_at).limit(1)
                    .with_for_update(skip_locked=True)
                )
                rows = (await conn.execute(sel)).all()
            else:
                # SQLite: no JSONB / no SKIP LOCKED. Scan ALL pending candidates in
                # FIFO order so a match after a mismatched prefix is still reached;
                # serialized writes make the subsequent CAS race-free.
                rows = (await conn.execute(
                    select(t).where(base).order_by(t.c.created_at)
                )).all()

            for row in rows:
                m = row._mapping
                task_labels = m["labels"] or {}
                if not all(worker_labels.get(k) == v for k, v in task_labels.items()):
                    continue  # (no-op on PG where SQL already filtered)
                now = _now()
                upd = await conn.execute(
                    update(t).where((t.c.run_id == m["run_id"]) & (t.c.status == "pending"))
                    .values(status="claimed", worker_id=worker_id, claimed_at=now,
                            lease_expires_at=self._lease_expr(), attempts=t.c.attempts + 1)
                )
                if upd.rowcount == 1:
                    return self._claimed_view(row, worker_id, now)
        return None

    async def report_task(self, run_id, status, output, error, *, worker_id) -> None:
        if status not in _TERMINAL_STATUSES:
            raise ValueError(
                f"Invalid report status {status!r} (only 'completed'/'failed' may be reported)"
            )
        from sqlalchemy import select, update

        t = self._tasks
        async with self._begin() as conn:
            upd = await conn.execute(
                update(t).where(
                    (t.c.run_id == run_id)
                    & (t.c.worker_id == worker_id)
                    & (t.c.status == "claimed")
                ).values(status=status, output=output, error=error,
                         reported_at=_now(), lease_expires_at=None)
            )
            if upd.rowcount == 1:
                return
            row = (await conn.execute(select(t).where(t.c.run_id == run_id))).first()
        if row is None:
            raise NotTaskOwnerError(run_id)
        m = row._mapping
        if m["worker_id"] != worker_id:
            raise NotTaskOwnerError(run_id)
        # already terminal: a same-worker + same-status repeat is an idempotent lost-ack.
        if m["status"] in _TERMINAL_STATUSES and m["status"] == status:
            return
        raise NotTaskOwnerError(run_id)

    async def renew_worker_leases(self, worker_id) -> int:
        from sqlalchemy import update

        t = self._tasks
        async with self._begin() as conn:
            res = await conn.execute(
                update(t).where((t.c.worker_id == worker_id) & (t.c.status == "claimed"))
                .values(lease_expires_at=self._lease_expr())
            )
        return res.rowcount

    async def reap_expired_leases(self, *, max_attempts=None) -> dict[str, int]:
        from sqlalchemy import update

        t = self._tasks
        cap = self._max_attempts if max_attempts is None else max_attempts
        now_expr = self._now_expr()
        async with self._begin() as conn:
            # Fail poison first so a capped row isn't requeued then re-failed in one tick.
            failed = (await conn.execute(
                update(t).where(
                    (t.c.status == "claimed") & (t.c.lease_expires_at < now_expr)
                    & (t.c.attempts >= cap)
                ).values(status="failed", error="lease expired after max attempts",
                         reported_at=now_expr, lease_expires_at=None)
            )).rowcount
            requeued = (await conn.execute(
                update(t).where(
                    (t.c.status == "claimed") & (t.c.lease_expires_at < now_expr)
                    & (t.c.attempts < cap)
                ).values(status="pending", worker_id=None, claimed_at=None,
                         lease_expires_at=None)
            )).rowcount
        return {"failed": failed, "requeued": requeued}
