"""Integration tests for Alembic migrations — requires running PostgreSQL."""

from __future__ import annotations

import os
import subprocess

import pytest

DB_URL = os.environ.get(
    "SAGEWAI_DATABASE_URL",
    "postgresql+asyncpg://sagecurator:sagecurator_password@localhost:5432/sagecurator",
)

# packages/sagewai/ directory (where alembic.ini lives)
_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    """Run an alembic command from the packages/sagewai directory."""
    env = {**os.environ, "SAGEWAI_DATABASE_URL": DB_URL}
    return subprocess.run(
        ["uv", "run", "alembic", "-c", "alembic.ini", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=_PKG_DIR,
    )


@pytest.mark.integration
class TestMigrationRoundTrip:
    def test_downgrade_to_base(self):
        result = _run_alembic("downgrade", "base")
        assert result.returncode == 0, result.stderr

    def test_upgrade_to_head(self):
        result = _run_alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr

    def test_full_round_trip(self):
        """downgrade base -> upgrade head -> downgrade base -> upgrade head."""
        for cmd in [
            ("downgrade", "base"),
            ("upgrade", "head"),
            ("downgrade", "base"),
            ("upgrade", "head"),
        ]:
            result = _run_alembic(*cmd)
            assert result.returncode == 0, f"{cmd}: {result.stderr}"

    def test_current_shows_head(self):
        _run_alembic("upgrade", "head")
        result = _run_alembic("current")
        assert result.returncode == 0
        assert "(head)" in result.stdout
