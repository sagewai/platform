# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for the production deployment safety validator.

``validate_production_config()`` is a fail-fast gate that runs only when
``SAGEWAI_ENV=production``. It must: pass with a fully-configured production
env, raise a single aggregated error listing every missing item otherwise, and
be a complete no-op when ``SAGEWAI_ENV`` is unset.
"""
from __future__ import annotations

import base64

import pytest

from sagewai.admin.prod_check import validate_production_config

# The env vars the validator reads — cleared between cases for isolation.
_ALL_KEYS = (
    "SAGEWAI_ENV",
    "SAGEWAI_TENANCY_MODE",
    "SAGEWAI_DATABASE_URL",
    "DATABASE_URL",
    "SAGEWAI_MASTER_KEY",
    "SAGEWAI_ALLOW_HOST_EXEC",
    "SAGEWAI_ADMIN_ALLOWED_ORIGINS",
    "SAGEWAI_ADMIN_TLS",
)


def _fernet_key() -> str:
    """A valid 32-byte url-safe base64 master key the resolver accepts."""
    return base64.urlsafe_b64encode(b"k" * 32).decode()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for k in _ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    # Hermetic master-key resolution: point HOME at an empty dir (no master.key
    # file) and neutralise the OS keychain so "no key" cases don't pick up the
    # developer's ambient key. SAGEWAI_MASTER_KEY (env) still resolves first.
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    monkeypatch.setattr("sagewai.sealed.master_key.keyring", None, raising=False)
    yield


def _set(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)


# ─────────────────────────── no-op when not prod ────────────────────────────


def test_noop_when_env_unset(monkeypatch):
    # SAGEWAI_ENV unset → no-op even with an otherwise-empty/unsafe environment.
    validate_production_config()  # must not raise


def test_noop_when_env_is_development(monkeypatch):
    _set(monkeypatch, SAGEWAI_ENV="development")
    validate_production_config()  # must not raise


def test_noop_when_env_is_test(monkeypatch):
    _set(monkeypatch, SAGEWAI_ENV="test")
    validate_production_config()  # must not raise


# ───────────────────────── passes when fully configured ─────────────────────


def test_passes_with_full_multi_production_env(monkeypatch):
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="multi",
        SAGEWAI_DATABASE_URL="postgresql+asyncpg://u:p@db:5432/sagewai",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="https://admin.example.com",
        SAGEWAI_ADMIN_TLS="1",
    )
    # SAGEWAI_ALLOW_HOST_EXEC deliberately unset (host exec OFF).
    validate_production_config()  # must not raise


def test_passes_with_full_single_org_production_env(monkeypatch):
    # single-org production: no DATABASE_URL / host-exec checks, but key + CORS
    # + TLS still required.
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="single",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="https://admin.example.com",
        SAGEWAI_ADMIN_TLS="1",
    )
    validate_production_config()  # must not raise


# ──────────────────────── raises listing specific gaps ──────────────────────


def test_empty_production_env_lists_every_problem(monkeypatch):
    _set(monkeypatch, SAGEWAI_ENV="production")
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    msg = str(exc.value)
    # All of the always-applicable checks must be named (mode unset → multi
    # branch is skipped, so no DB/host-exec problem here).
    assert "SAGEWAI_TENANCY_MODE is not set" in msg
    assert "no master key is resolvable" in msg
    assert "SAGEWAI_ADMIN_ALLOWED_ORIGINS" in msg
    assert "SAGEWAI_ADMIN_TLS is not enabled" in msg


def test_multi_without_database_url_is_flagged(monkeypatch):
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="multi",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="https://admin.example.com",
        SAGEWAI_ADMIN_TLS="1",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    assert "Postgres DATABASE_URL" in str(exc.value)


def test_multi_with_non_postgres_database_url_is_flagged(monkeypatch):
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="multi",
        SAGEWAI_DATABASE_URL="sqlite+aiosqlite:///x.db",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="https://admin.example.com",
        SAGEWAI_ADMIN_TLS="1",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    assert "not a Postgres URL" in str(exc.value)


def test_multi_with_host_exec_enabled_is_flagged(monkeypatch):
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="multi",
        SAGEWAI_DATABASE_URL="postgresql+asyncpg://u:p@db/sagewai",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ALLOW_HOST_EXEC="1",
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="https://admin.example.com",
        SAGEWAI_ADMIN_TLS="1",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    assert "SAGEWAI_ALLOW_HOST_EXEC is enabled" in str(exc.value)


def test_localhost_cors_default_is_flagged(monkeypatch):
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="single",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="http://localhost:3008,http://127.0.0.1:3008",
        SAGEWAI_ADMIN_TLS="1",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    assert "SAGEWAI_ADMIN_ALLOWED_ORIGINS" in str(exc.value)


def test_tls_off_is_flagged(monkeypatch):
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="single",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="https://admin.example.com",
        # SAGEWAI_ADMIN_TLS unset
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    assert "SAGEWAI_ADMIN_TLS is not enabled" in str(exc.value)


def test_unrecognised_mode_is_flagged(monkeypatch):
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="bogus",
        SAGEWAI_MASTER_KEY=_fernet_key(),
        SAGEWAI_ADMIN_ALLOWED_ORIGINS="https://admin.example.com",
        SAGEWAI_ADMIN_TLS="1",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    assert "not a recognised value" in str(exc.value)


def test_error_aggregates_multiple_problems(monkeypatch):
    # multi, no DB, no key, host-exec on, no CORS, no TLS → many problems in ONE error.
    _set(
        monkeypatch,
        SAGEWAI_ENV="production",
        SAGEWAI_TENANCY_MODE="multi",
        SAGEWAI_ALLOW_HOST_EXEC="true",
    )
    with pytest.raises(RuntimeError) as exc:
        validate_production_config()
    msg = str(exc.value)
    assert "Postgres DATABASE_URL" in msg
    assert "no master key is resolvable" in msg
    assert "SAGEWAI_ALLOW_HOST_EXEC is enabled" in msg
    assert "SAGEWAI_ADMIN_ALLOWED_ORIGINS" in msg
    assert "SAGEWAI_ADMIN_TLS is not enabled" in msg
    # It is a single aggregated raise, naming the count.
    assert "problem(s)" in msg
