# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shared fixtures for admin tests.

Provides a stable per-test master key so encryption tests don't write to
~/.sagewai and make encryption deterministic.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _admin_test_master_key(monkeypatch):
    """Inject a fresh Fernet key for every admin test.

    Tests that need the key-absent behaviour call
    ``monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)``
    in their own body; monkeypatch ordering ensures the test-local
    delenv takes precedence over this autouse fixture.
    """
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    yield
