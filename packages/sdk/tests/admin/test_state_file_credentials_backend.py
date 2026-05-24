# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AdminStateFile.default_credentials_backend tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from sagewai.admin.state_file import AdminStateFile


def test_default_credentials_backend_defaults_to_local(tmp_path: Path):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    assert sf.get_default_credentials_backend() == "local"


def test_set_default_credentials_backend_to_env(tmp_path: Path):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    sf.set_default_credentials_backend("env")
    assert sf.get_default_credentials_backend() == "env"
    # Persisted: reload from disk
    sf2 = AdminStateFile(tmp_path / "admin-state.json")
    assert sf2.get_default_credentials_backend() == "env"


def test_set_default_credentials_backend_to_sops(tmp_path: Path):
    sf = AdminStateFile(tmp_path / "admin-state.json")
    sf.set_default_credentials_backend("sops")
    assert sf.get_default_credentials_backend() == "sops"


def test_set_default_credentials_backend_rejects_unknown(tmp_path: Path):
    from sagewai.connections.credentials import UnknownBackendError
    sf = AdminStateFile(tmp_path / "admin-state.json")
    with pytest.raises(UnknownBackendError):
        sf.set_default_credentials_backend("not-a-backend")


def test_get_when_existing_state_file_has_no_field(tmp_path: Path):
    """Backward-compatible read: existing state files default to 'local'."""
    path = tmp_path / "admin-state.json"
    # Write a state file in the old shape (no default_credentials_backend)
    import json
    path.write_text(json.dumps({"setup_complete": True}))
    sf = AdminStateFile(path)
    assert sf.get_default_credentials_backend() == "local"
