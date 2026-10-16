# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable project-scoped local training receipts and their single worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class TrainingSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=65536)
    output: str = Field(min_length=1, max_length=65536)


class TrainingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=1, max_length=256)
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_model: str = Field(min_length=1, max_length=256)
    samples: list[TrainingSample] = Field(min_length=1, max_length=10000)


class JobConflictError(Exception):
    """A durable job identity already belongs to different input."""


def scrub_inputs(directory: Path, ident: str):
    """Retain trained weights and lineage, never terminal plaintext training rows."""
    (directory / "request.json").unlink(missing_ok=True)
    (directory / "model" / ident / "dataset.jsonl").unlink(missing_ok=True)


class TrainingJobs:
    """SQLite receipts; every externally addressed operation includes org/project."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / "jobs.sqlite3"
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                org_id TEXT NOT NULL, project_id TEXT NOT NULL, id TEXT NOT NULL,
                payload TEXT NOT NULL, request_hash TEXT, samples TEXT,
                created_at INTEGER NOT NULL,
                status TEXT NOT NULL, reason TEXT, candidate_model TEXT,
                endpoint_url TEXT, digest TEXT, context_window INTEGER,
                PRIMARY KEY (org_id, project_id, id))""")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row):
        if row is None:
            return None
        data = json.loads(row["payload"])
        return {
            **{
                k: row[k]
                for k in (
                    "id",
                    "project_id",
                    "status",
                    "reason",
                    "candidate_model",
                    "endpoint_url",
                    "digest",
                    "context_window",
                )
            },
            **data,
        }

    def directory(self, org, project, ident):
        key = hashlib.sha256("\0".join((org, project, ident)).encode()).hexdigest()
        return self.root / key

    def get(self, org: str, project: str, ident: str):
        with self.connection() as db:
            return self.public(
                db.execute(
                    "SELECT * FROM jobs WHERE org_id=? AND project_id=? AND id=?",
                    (org, project, ident),
                ).fetchone()
            )

    def submit(self, org: str, project: str, ident: str, request: TrainingRequest):
        data = request.model_dump()
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()
        samples = json.dumps(data.pop("samples"), ensure_ascii=False)
        payload = json.dumps(data, ensure_ascii=False)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM jobs WHERE org_id=? AND project_id=? AND id=?", (org, project, ident)
            ).fetchone()
            if old is not None:
                if old["request_hash"] is None:
                    db.execute(
                        "UPDATE jobs SET payload=?,request_hash=? WHERE org_id=? AND project_id=? AND id=?",
                        (payload, request_hash, org, project, ident),
                    )
                    return self.public(
                        db.execute(
                            "SELECT * FROM jobs WHERE org_id=? AND project_id=? AND id=?",
                            (org, project, ident),
                        ).fetchone()
                    )
                if old["request_hash"] != request_hash:
                    raise JobConflictError(
                        "This job ID already belongs to different training input"
                    )
                return self.public(old)
            db.execute(
                "INSERT INTO jobs(org_id,project_id,id,payload,request_hash,samples,created_at,status) VALUES(?,?,?,?,?,?,?,'queued')",
                (org, project, ident, payload, request_hash, samples, time.time_ns()),
            )
        return self.get(org, project, ident)

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                return None
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='running' WHERE org_id=? AND project_id=? AND id=?",
                (row["org_id"], row["project_id"], row["id"]),
            )
            return {
                "org_id": row["org_id"],
                "id": row["id"],
                "project_id": row["project_id"],
                **json.loads(row["payload"]),
                "samples": json.loads(row["samples"]),
            }

    def recover(self):
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status='failed',reason='Local training was interrupted; this job will not be run again' WHERE status='running'"
            )
            db.execute("UPDATE jobs SET samples=NULL WHERE status NOT IN ('queued','running')")
            terminal = db.execute(
                "SELECT org_id,project_id,id FROM jobs WHERE status NOT IN ('queued','running')"
            ).fetchall()
        for row in terminal:
            scrub_inputs(self.directory(*row), row["id"])

    def finish(self, job, result=None, *, reason=None):
        result = result or {}
        with self.connection() as db:
            db.execute(
                """UPDATE jobs SET status=?,reason=?,candidate_model=?,endpoint_url=?,digest=?,context_window=?,samples=NULL
                WHERE org_id=? AND project_id=? AND id=? AND status='running'""",
                (
                    "failed" if reason else "completed",
                    reason,
                    result.get("candidate_model"),
                    result.get("endpoint_url"),
                    result.get("digest"),
                    result.get("context_window"),
                    job["org_id"],
                    job["project_id"],
                    job["id"],
                ),
            )
        scrub_inputs(self.directory(job["org_id"], job["project_id"], job["id"]), job["id"])

    def cancel(self, org, project, ident):
        with self.connection() as db:
            db.execute(
                """INSERT INTO jobs(org_id,project_id,id,payload,created_at,status,reason)
                VALUES(?,?,?,'{}',?,'cancelled','Training cancelled')
                ON CONFLICT(org_id,project_id,id) DO UPDATE SET
                status='cancelled',reason='Training cancelled',samples=NULL,
                candidate_model=NULL,endpoint_url=NULL,digest=NULL,context_window=NULL""",
                (org, project, ident, time.time_ns()),
            )
        return self.get(org, project, ident)


class TrainingWorker:
    """One process-owned worker; running work is never replayed after interruption."""

    def __init__(
        self, jobs: TrainingJobs, *, execute: Callable[[dict], Awaitable[dict]] | None = None
    ):
        self.jobs = jobs
        self.execute = execute or self._subprocess
        self.task = None
        self.active = None
        self.execution = None
        self.lock = None
        self.closing = False
        self.cancellation_requested = False

    async def start(self):
        self.closing = False
        self.lock = sqlite3.connect(self.jobs.root / "worker.lock", timeout=0)
        try:
            self.lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            self.lock.close()
            self.lock = None
            raise RuntimeError("Local training worker lock is unavailable") from exc
        self.jobs.recover()
        self.task = asyncio.create_task(self._work())

    async def close(self):
        self.closing = True
        if self.task is not None:
            if self.execution is None:
                self.task.cancel()
            elif not self.cancellation_requested:
                self.cancellation_requested = True
                self.execution.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            self.jobs.recover()
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    async def cancel(self, org, project, ident):
        result = self.jobs.cancel(org, project, ident)
        if (
            self.active == (org, project, ident)
            and self.execution is not None
            and not self.cancellation_requested
        ):
            self.cancellation_requested = True
            self.execution.cancel()
        elif self.active != (org, project, ident):
            scrub_inputs(self.jobs.directory(org, project, ident), ident)
        return result

    async def _work(self):
        while not self.closing:
            job = self.jobs.claim()
            if job is None:
                await asyncio.sleep(0.2)
                continue
            self.active = (job["org_id"], job["project_id"], job["id"])
            self.execution = asyncio.create_task(self.execute(job))
            try:
                result = await self.execution
                self.jobs.finish(job, result)
            except asyncio.CancelledError:
                if self.closing or self.jobs.get(*self.active)["status"] != "cancelled":
                    raise
            except Exception as exc:
                self.jobs.finish(job, reason=str(exc) or type(exc).__name__)
            finally:
                scrub_inputs(self.jobs.directory(*self.active), job["id"])
                self.active = self.execution = None
                self.cancellation_requested = False

    async def _subprocess(self, job):
        from .local_training_executor import local_configuration

        models, endpoint = local_configuration()
        model = models.get(job["base_model"])
        if model is None:
            raise RuntimeError(
                "The base model has no configured installed local training directory"
            )
        directory = self.jobs.directory(job["org_id"], job["project_id"], job["id"])
        directory.mkdir(mode=0o700)
        request_path, result_path = directory / "request.json", directory / "result.json"
        request_path.write_text(
            json.dumps(
                {
                    "job": job,
                    "model_path": str(model),
                    "output_dir": str(directory),
                    "ollama_url": endpoint,
                    "candidate": "sagewai-trained-" + directory.name + ":latest",
                    "parent_pid": os.getpid(),
                }
            )
        )
        request_path.chmod(0o600)
        env = {
            key: os.environ[key]
            for key in (
                "PATH",
                "PYTHONPATH",
                "LANG",
                "LC_ALL",
                "LD_LIBRARY_PATH",
                "DYLD_LIBRARY_PATH",
                "CUDA_VISIBLE_DEVICES",
                "LLAMA_CPP_DIR",
                "SAGEWAI_FT_GGUF_QUANT",
            )
            if key in os.environ
        }
        env.update(
            {
                "HOME": str(directory),
                "TMPDIR": str(directory),
                "HF_HOME": str(directory / "hf"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
            }
        )
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "sagewai.admin.local_training_executor",
                str(request_path),
                str(result_path),
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        )
        proc = None
        try:
            proc = await asyncio.shield(spawning)
            await proc.wait()
        except asyncio.CancelledError:
            if proc is None:
                proc = await spawning
            if proc.returncode is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
            raise
        if not result_path.is_file():
            raise RuntimeError("Local training process stopped without a completion receipt")
        result = json.loads(result_path.read_text())
        if "error" in result:
            raise RuntimeError(result["error"])
        return result
