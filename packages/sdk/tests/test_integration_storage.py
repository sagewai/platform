"""Integration tests for S3 and GCS archive backends.

Requires LocalStack (port 4566) and fake-gcs-server (port 4443).
Run with: make test-storage

These tests are skipped if the services aren't running.
"""

from __future__ import annotations

import json
import os

import pytest


def _has_boto3() -> bool:
    try:
        import boto3  # noqa: F401

        return True
    except ImportError:
        return False


def _localstack_available() -> bool:
    """Check whether LocalStack is reachable on localhost:4566 and boto3 is installed."""
    if not _has_boto3():
        return False
    try:
        import httpx

        r = httpx.get("http://localhost:4566/_localstack/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _fake_gcs_available() -> bool:
    """Check whether fake-gcs-server is reachable on localhost:4443."""
    try:
        import httpx

        r = httpx.get("http://localhost:4443/storage/v1/b", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


localstack_available = pytest.mark.skipif(
    not _localstack_available(),
    reason="LocalStack not running or boto3 not installed",
)

fake_gcs_available = pytest.mark.skipif(
    not _fake_gcs_available(),
    reason="fake-gcs-server not running on localhost:4443",
)


BUCKET = "sagewai-test-archives"


@localstack_available
class TestS3ArchiveBackend:
    """Integration tests for S3ArchiveBackend against LocalStack."""

    @pytest.fixture(autouse=True)
    def setup_bucket(self):
        """Create test bucket in LocalStack."""
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url="http://localhost:4566",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        try:
            client.create_bucket(Bucket=BUCKET)
        except client.exceptions.BucketAlreadyOwnedByYou:
            pass
        yield
        # Cleanup: delete all objects
        try:
            resp = client.list_objects_v2(Bucket=BUCKET)
            for obj in resp.get("Contents", []):
                client.delete_object(Bucket=BUCKET, Key=obj["Key"])
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_write_and_read(self):
        """Write data to S3 and read it back."""
        from sagewai.observability.archiver import S3ArchiveBackend

        os.environ["AWS_ACCESS_KEY_ID"] = "test"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"

        backend = S3ArchiveBackend(
            bucket=BUCKET,
            region="us-east-1",
            endpoint_url="http://localhost:4566",
        )

        data = b'{"key": "value"}\n{"key2": "value2"}\n'
        written = await backend.write("test/data.jsonl", data)
        assert written == len(data)

        read_back = await backend.read("test/data.jsonl")
        assert read_back == data

    @pytest.mark.asyncio
    async def test_list_files(self):
        """List files under a prefix."""
        from sagewai.observability.archiver import S3ArchiveBackend

        os.environ["AWS_ACCESS_KEY_ID"] = "test"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"

        backend = S3ArchiveBackend(
            bucket=BUCKET,
            region="us-east-1",
            endpoint_url="http://localhost:4566",
        )

        await backend.write("archive/2024/file1.jsonl", b"data1")
        await backend.write("archive/2024/file2.jsonl", b"data2")
        await backend.write("other/file3.jsonl", b"data3")

        files = await backend.list_files("archive/2024/")
        assert len(files) == 2
        assert "archive/2024/file1.jsonl" in files
        assert "archive/2024/file2.jsonl" in files

    @pytest.mark.asyncio
    async def test_exists(self):
        """Check file existence."""
        from sagewai.observability.archiver import S3ArchiveBackend

        os.environ["AWS_ACCESS_KEY_ID"] = "test"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"

        backend = S3ArchiveBackend(
            bucket=BUCKET,
            region="us-east-1",
            endpoint_url="http://localhost:4566",
        )

        assert not await backend.exists("nonexistent.jsonl")
        await backend.write("exists-test.jsonl", b"data")
        assert await backend.exists("exists-test.jsonl")

    @pytest.mark.asyncio
    async def test_prefix_support(self):
        """Prefix is prepended to all paths."""
        from sagewai.observability.archiver import S3ArchiveBackend

        os.environ["AWS_ACCESS_KEY_ID"] = "test"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"

        backend = S3ArchiveBackend(
            bucket=BUCKET,
            region="us-east-1",
            prefix="sagewai/",
            endpoint_url="http://localhost:4566",
        )

        await backend.write("test.jsonl", b"prefixed data")
        read_back = await backend.read("test.jsonl")
        assert read_back == b"prefixed data"

    @pytest.mark.asyncio
    async def test_jsonl_round_trip(self):
        """JSONL serialization round-trip through S3."""
        from sagewai.observability.archiver import S3ArchiveBackend, _to_jsonl

        os.environ["AWS_ACCESS_KEY_ID"] = "test"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test"

        backend = S3ArchiveBackend(
            bucket=BUCKET,
            region="us-east-1",
            endpoint_url="http://localhost:4566",
        )

        records = [
            {"workflow": "pipeline", "status": "completed", "cost": 0.05},
            {"workflow": "pipeline", "status": "failed", "error": "timeout"},
        ]

        data = _to_jsonl(records)
        await backend.write("workflows/2024-01-01/runs.jsonl", data)

        read_back = await backend.read("workflows/2024-01-01/runs.jsonl")
        lines = read_back.decode().strip().split("\n")
        assert len(lines) == 2
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["workflow"] == "pipeline"
        assert parsed[1]["error"] == "timeout"


@fake_gcs_available
class TestGCSArchiveBackend:
    """Integration tests for GCSArchiveBackend against fake-gcs-server."""

    @pytest.fixture(autouse=True)
    def setup_bucket(self):
        """Create test bucket in fake-gcs-server."""
        import httpx

        try:
            httpx.post(
                "http://localhost:4443/storage/v1/b?project=test",
                json={"name": BUCKET},
                timeout=5,
            )
        except Exception:
            pass
        yield

    @pytest.mark.asyncio
    async def test_write_and_read(self):
        """Write data to GCS and read it back."""
        from sagewai.observability.archiver import GCSArchiveBackend

        backend = GCSArchiveBackend(
            bucket=BUCKET,
            endpoint_url="http://localhost:4443",
        )

        data = b'{"event": "test"}\n'
        written = await backend.write("test/events.jsonl", data)
        assert written == len(data)

        read_back = await backend.read("test/events.jsonl")
        assert read_back == data

    @pytest.mark.asyncio
    async def test_list_files(self):
        """List files under a prefix in GCS."""
        from sagewai.observability.archiver import GCSArchiveBackend

        backend = GCSArchiveBackend(
            bucket=BUCKET,
            endpoint_url="http://localhost:4443",
        )

        await backend.write("archive/a.jsonl", b"data1")
        await backend.write("archive/b.jsonl", b"data2")

        files = await backend.list_files("archive/")
        assert len(files) >= 2

    @pytest.mark.asyncio
    async def test_exists(self):
        """Check file existence in GCS."""
        from sagewai.observability.archiver import GCSArchiveBackend

        backend = GCSArchiveBackend(
            bucket=BUCKET,
            endpoint_url="http://localhost:4443",
        )

        assert not await backend.exists("gcs-nonexistent.jsonl")
        await backend.write("gcs-exists-test.jsonl", b"data")
        assert await backend.exists("gcs-exists-test.jsonl")

    @pytest.mark.asyncio
    async def test_jsonl_round_trip(self):
        """JSONL round-trip through GCS."""
        from sagewai.observability.archiver import GCSArchiveBackend, _to_jsonl

        backend = GCSArchiveBackend(
            bucket=BUCKET,
            endpoint_url="http://localhost:4443",
        )

        records = [{"agent": "researcher", "tokens": 1500}]
        data = _to_jsonl(records)
        await backend.write("prompts/test.jsonl", data)

        read_back = await backend.read("prompts/test.jsonl")
        parsed = json.loads(read_back.decode().strip())
        assert parsed["agent"] == "researcher"


@localstack_available
@fake_gcs_available
class TestArchiverWithConfig:
    """Test ArchiveConfig endpoint_url integration."""

    @pytest.mark.asyncio
    async def test_s3_config_creates_backend_with_endpoint(self):
        """ArchiveConfig with endpoint_url creates S3 backend."""
        from sagewai.observability.archiver import (
            ArchiveConfig,
            S3ArchiveBackend,
            _create_backend,
        )

        config = ArchiveConfig(
            backend="s3",
            bucket=BUCKET,
            region="us-east-1",
            endpoint_url="http://localhost:4566",
        )
        backend = _create_backend(config)
        assert isinstance(backend, S3ArchiveBackend)
        assert backend._endpoint_url == "http://localhost:4566"

    @pytest.mark.asyncio
    async def test_gcs_config_creates_backend_with_endpoint(self):
        """ArchiveConfig with endpoint_url creates GCS backend."""
        from sagewai.observability.archiver import (
            ArchiveConfig,
            GCSArchiveBackend,
            _create_backend,
        )

        config = ArchiveConfig(
            backend="gcs",
            bucket=BUCKET,
            endpoint_url="http://localhost:4443",
        )
        backend = _create_backend(config)
        assert isinstance(backend, GCSArchiveBackend)
        assert backend._endpoint_url == "http://localhost:4443"
