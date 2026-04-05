# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Data lifecycle management — archive old data to object storage.

Moves aged-out data from PostgreSQL to object storage (S3, GCS, or local
filesystem) in JSONL and Parquet formats. Supports configurable retention
policies and periodic archival runs.

Usage::

    from sagewai.observability.archiver import Archiver, ArchiveConfig

    config = ArchiveConfig(
        backend="local",
        base_path="/tmp/sagewai-archives",
        prompt_retention_days=30,
        workflow_retention_days=90,
    )
    archiver = Archiver(config=config, store=postgres_store)

    # Run one archival cycle
    stats = await archiver.run()
    print(f"Archived {stats.prompts_archived} prompts, {stats.runs_archived} runs")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sagewai.core.context import resolve_project_id

logger = logging.getLogger(__name__)


class ArchiveConfig(BaseModel):
    """Configuration for data archival."""

    backend: str = "local"  # "local", "s3", "gcs"
    base_path: str = "./archives"  # local path or bucket name
    region: str = ""  # AWS region for S3

    # Retention policies (days to keep in Postgres before archiving)
    prompt_retention_days: int = 30
    workflow_retention_days: int = 90
    audit_retention_days: int = 365

    # Archive formats
    prompt_format: str = "jsonl"  # "jsonl" or "parquet"
    workflow_format: str = "jsonl"
    metrics_format: str = "parquet"

    # S3/GCS specific
    bucket: str = ""
    prefix: str = "sagewai/"
    endpoint_url: str = ""  # Custom endpoint for LocalStack / fake-gcs-server

    # Backup
    backup_enabled: bool = True


@dataclass
class ArchiveStats:
    """Statistics from an archival run."""

    prompts_archived: int = 0
    runs_archived: int = 0
    events_archived: int = 0
    bytes_written: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class ArchiveBackend:
    """Base class for archive storage backends."""

    async def write(self, path: str, data: bytes) -> int:
        """Write data to the given path. Returns bytes written."""
        raise NotImplementedError

    async def read(self, path: str) -> bytes:
        """Read data from the given path."""
        raise NotImplementedError

    async def list_files(self, prefix: str) -> list[str]:
        """List files under the given prefix."""
        raise NotImplementedError

    async def exists(self, path: str) -> bool:
        """Check if a path exists."""
        raise NotImplementedError


class LocalArchiveBackend(ArchiveBackend):
    """Local filesystem archive backend for dev/testing."""

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)

    async def write(self, path: str, data: bytes) -> int:
        full_path = self._base / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)
        return len(data)

    async def read(self, path: str) -> bytes:
        return (self._base / path).read_bytes()

    async def list_files(self, prefix: str) -> list[str]:
        target = self._base / prefix
        if not target.exists():
            return []
        return [
            str(p.relative_to(self._base))
            for p in target.rglob("*")
            if p.is_file()
        ]

    async def exists(self, path: str) -> bool:
        return (self._base / path).exists()


class S3ArchiveBackend(ArchiveBackend):
    """AWS S3 / S3-compatible archive backend.

    Requires boto3 (optional dependency).
    """

    def __init__(
        self,
        bucket: str,
        region: str = "",
        prefix: str = "",
        endpoint_url: str = "",
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._prefix = prefix
        self._endpoint_url = endpoint_url
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 required for S3 archival. "
                    "Install with: uv add boto3"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self._region:
                kwargs["region_name"] = self._region
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            self._client = boto3.client("s3", **kwargs)
        return self._client

    async def write(self, path: str, data: bytes) -> int:
        full_key = f"{self._prefix}{path}" if self._prefix else path
        self._get_client().put_object(
            Bucket=self._bucket, Key=full_key, Body=data
        )
        return len(data)

    async def read(self, path: str) -> bytes:
        full_key = f"{self._prefix}{path}" if self._prefix else path
        response = self._get_client().get_object(
            Bucket=self._bucket, Key=full_key
        )
        return response["Body"].read()

    async def list_files(self, prefix: str) -> list[str]:
        full_prefix = (
            f"{self._prefix}{prefix}" if self._prefix else prefix
        )
        client = self._get_client()
        paginator = client.get_paginator("list_objects_v2")
        files = []
        for page in paginator.paginate(
            Bucket=self._bucket, Prefix=full_prefix
        ):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if self._prefix:
                    key = key[len(self._prefix) :]
                files.append(key)
        return files

    async def exists(self, path: str) -> bool:
        full_key = f"{self._prefix}{path}" if self._prefix else path
        try:
            self._get_client().head_object(
                Bucket=self._bucket, Key=full_key
            )
            return True
        except Exception:  # noqa: broad-exception-caught — treat any error as not-exists
            return False


class GCSArchiveBackend(ArchiveBackend):
    """Google Cloud Storage archive backend.

    Requires google-cloud-storage (optional dependency).
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str = "",
    ) -> None:
        self._bucket_name = bucket
        self._prefix = prefix
        self._endpoint_url = endpoint_url
        self._bucket: Any = None

    def _get_bucket(self) -> Any:
        if self._bucket is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise ImportError(
                    "google-cloud-storage required for GCS archival. "
                    "Install with: uv add google-cloud-storage"
                ) from exc
            if self._endpoint_url:
                from google.auth.credentials import AnonymousCredentials

                client = storage.Client(
                    project="test",
                    credentials=AnonymousCredentials(),
                    client_options={"api_endpoint": self._endpoint_url},
                )
            else:
                client = storage.Client()
            self._bucket = client.bucket(self._bucket_name)
        return self._bucket

    async def write(self, path: str, data: bytes) -> int:
        full_path = f"{self._prefix}{path}" if self._prefix else path
        blob = self._get_bucket().blob(full_path)
        blob.upload_from_string(data)
        return len(data)

    async def read(self, path: str) -> bytes:
        full_path = f"{self._prefix}{path}" if self._prefix else path
        blob = self._get_bucket().blob(full_path)
        return blob.download_as_bytes()

    async def list_files(self, prefix: str) -> list[str]:
        full_prefix = (
            f"{self._prefix}{prefix}" if self._prefix else prefix
        )
        blobs = self._get_bucket().list_blobs(prefix=full_prefix)
        files = []
        for blob in blobs:
            name = blob.name
            if self._prefix:
                name = name[len(self._prefix) :]
            files.append(name)
        return files

    async def exists(self, path: str) -> bool:
        full_path = f"{self._prefix}{path}" if self._prefix else path
        return self._get_bucket().blob(full_path).exists()


def _create_backend(config: ArchiveConfig) -> ArchiveBackend:
    """Create archive backend from config."""
    if config.backend == "s3":
        return S3ArchiveBackend(
            bucket=config.bucket or config.base_path,
            region=config.region,
            prefix=config.prefix,
            endpoint_url=config.endpoint_url,
        )
    elif config.backend == "gcs":
        return GCSArchiveBackend(
            bucket=config.bucket or config.base_path,
            prefix=config.prefix,
            endpoint_url=config.endpoint_url,
        )
    else:
        return LocalArchiveBackend(config.base_path)


def _to_jsonl(records: list[dict[str, Any]]) -> bytes:
    """Convert records to JSONL bytes."""
    lines = [json.dumps(r, default=str) for r in records]
    return ("\n".join(lines) + "\n").encode()


def _to_parquet(records: list[dict[str, Any]]) -> bytes:
    """Convert records to Parquet bytes. Requires pyarrow."""
    try:
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist(records)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="snappy")
        return buf.getvalue()
    except ImportError:
        # Fallback to JSONL if pyarrow not available
        logger.warning(
            "pyarrow not installed, falling back to JSONL format"
        )
        return _to_jsonl(records)


class Archiver:
    """Data lifecycle manager — archives old data to object storage.

    Parameters
    ----------
    config:
        Archive configuration.
    store:
        PostgresStore (or compatible) for querying data to archive.
    project_id:
        Optional project identifier. When set, archival only touches
        data belonging to this project and writes to a project-scoped
        path prefix.
    """

    def __init__(
        self, config: ArchiveConfig, store: Any, *, project_id: str | None = None
    ) -> None:
        self._config = config
        self._store = store
        self._backend = _create_backend(config)
        self._project_id = project_id

    async def run(self, *, project_id: str | None = None) -> ArchiveStats:
        """Run one archival cycle.

        Archives data older than retention thresholds, then
        optionally deletes archived data from Postgres.

        Parameters
        ----------
        project_id:
            Override the instance-level project_id for this run.
        """
        pid = resolve_project_id(project_id or self._project_id)
        stats = ArchiveStats()
        t0 = time.monotonic()

        try:
            await self._archive_workflow_runs(stats, pid)
        except Exception as exc:  # noqa: broad-exception-caught — archival must not crash the run
            logger.exception("Failed to archive workflow runs")
            stats.errors.append(f"workflow_runs: {exc}")

        try:
            await self._archive_workflow_events(stats, pid)
        except Exception as exc:  # noqa: broad-exception-caught — archival must not crash the run
            logger.exception("Failed to archive workflow events")
            stats.errors.append(f"workflow_events: {exc}")

        stats.duration_seconds = time.monotonic() - t0
        logger.info(
            "Archival complete: %d runs, %d events archived "
            "(%d bytes, %.1fs, %d errors)",
            stats.runs_archived,
            stats.events_archived,
            stats.bytes_written,
            stats.duration_seconds,
            len(stats.errors),
        )
        return stats

    async def _archive_workflow_runs(
        self, stats: ArchiveStats, project_id: str = "default"
    ) -> None:
        """Archive completed workflow runs older than retention."""
        if not hasattr(self._store, "_pool"):
            return

        retention_days = self._config.workflow_retention_days
        rows = await self._store._pool.fetch(
            """
            SELECT id, workflow_name, run_id, status, data,
                   input, output, error, created_at, updated_at
            FROM workflow_runs
            WHERE status IN ('completed', 'failed', 'cancelled')
              AND project_id = $2
              AND updated_at < NOW() - MAKE_INTERVAL(days => $1)
            ORDER BY created_at ASC
            LIMIT 1000
            """,
            retention_days,
            project_id,
        )

        if not rows:
            return

        records = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
            inp = row["input"]
            if isinstance(inp, str):
                inp = json.loads(inp) if inp else {}
            out = row["output"]
            if isinstance(out, str):
                out = json.loads(out) if out else None
            records.append({
                "id": row["id"],
                "workflow_name": row["workflow_name"],
                "run_id": row["run_id"],
                "status": row["status"],
                "input": inp,
                "output": out,
                "error": row["error"],
                "data": data,
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            })

        # Write to storage (project-scoped path)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fmt = self._config.workflow_format
        if fmt == "parquet":
            data_bytes = _to_parquet(records)
            ext = "parquet"
        else:
            data_bytes = _to_jsonl(records)
            ext = "jsonl"

        path = f"{project_id}/workflows/{date_str}/runs.{ext}"
        written = await self._backend.write(path, data_bytes)
        stats.runs_archived += len(records)
        stats.bytes_written += written

        # Delete archived rows from Postgres
        ids = [row["id"] for row in rows]
        await self._store._pool.execute(
            "DELETE FROM workflow_runs WHERE id = ANY($1)",
            ids,
        )
        logger.info(
            "Archived %d workflow runs to %s", len(records), path
        )

    async def _archive_workflow_events(
        self, stats: ArchiveStats, project_id: str = "default"
    ) -> None:
        """Archive workflow events older than retention."""
        if not hasattr(self._store, "_pool"):
            return

        retention_days = self._config.workflow_retention_days
        rows = await self._store._pool.fetch(
            """
            SELECT we.id, we.run_id, we.event_type, we.data, we.created_at
            FROM workflow_events we
            INNER JOIN workflow_runs wr ON wr.run_id = we.run_id
            WHERE wr.project_id = $2
              AND we.created_at < NOW() - MAKE_INTERVAL(days => $1)
            ORDER BY we.id ASC
            LIMIT 5000
            """,
            retention_days,
            project_id,
        )

        if not rows:
            return

        records = []
        for row in rows:
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
            records.append({
                "id": row["id"],
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "data": data,
                "created_at": str(row["created_at"]),
            })

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fmt = self._config.metrics_format
        if fmt == "parquet":
            data_bytes = _to_parquet(records)
            ext = "parquet"
        else:
            data_bytes = _to_jsonl(records)
            ext = "jsonl"

        path = f"{project_id}/events/{date_str}/events.{ext}"
        written = await self._backend.write(path, data_bytes)
        stats.events_archived += len(records)
        stats.bytes_written += written

        # Delete archived events
        ids = [row["id"] for row in rows]
        await self._store._pool.execute(
            "DELETE FROM workflow_events WHERE id = ANY($1)",
            ids,
        )
        logger.info(
            "Archived %d workflow events to %s", len(records), path
        )

    async def backup_manifest(self) -> dict[str, Any]:
        """Get or create the backup manifest."""
        manifest_path = "backups/manifest.json"
        if await self._backend.exists(manifest_path):
            data = await self._backend.read(manifest_path)
            return json.loads(data)
        return {
            "backups": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
