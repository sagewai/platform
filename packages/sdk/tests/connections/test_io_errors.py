# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for connection import/export error hierarchy."""
from __future__ import annotations

from sagewai.connections.io_errors import (
    ImportDefaultCollisionError,
    ImportDisplayNameCollisionError,
    ImportEnvVarMissingError,
    ImportError as ConnImportError,
    ImportIdCollisionError,
    ImportMasterKeyMismatchError,
    ImportProtocolDataInvalidError,
    ImportUnknownBackendError,
    ImportUnknownProtocolError,
    ImportUnknownVersionError,
    ImportYamlParseError,
)


def test_error_hierarchy():
    """All 10 import errors subclass the base ImportError."""
    for cls in (
        ImportYamlParseError,
        ImportUnknownVersionError,
        ImportUnknownProtocolError,
        ImportProtocolDataInvalidError,
        ImportUnknownBackendError,
        ImportDisplayNameCollisionError,
        ImportEnvVarMissingError,
        ImportIdCollisionError,
        ImportDefaultCollisionError,
        ImportMasterKeyMismatchError,
    ):
        assert issubclass(cls, ConnImportError), cls.__name__


def test_error_codes_stable():
    """All codes are stable strings, matching the spec table."""
    assert ConnImportError.code == "import_error"
    assert ImportYamlParseError.code == "import_yaml_parse_error"
    assert ImportUnknownVersionError.code == "import_unknown_version"
    assert ImportUnknownProtocolError.code == "import_unknown_protocol"
    assert ImportProtocolDataInvalidError.code == "import_protocol_data_invalid"
    assert ImportUnknownBackendError.code == "import_unknown_backend"
    assert ImportDisplayNameCollisionError.code == "import_display_name_collision"
    assert ImportEnvVarMissingError.code == "import_env_var_missing"
    assert ImportIdCollisionError.code == "import_id_collision"
    assert ImportDefaultCollisionError.code == "import_default_collision"
    assert ImportMasterKeyMismatchError.code == "import_master_key_mismatch"


def test_yaml_parse_error_carries_line():
    err = ImportYamlParseError(line=42, message="found unexpected key")
    assert err.line == 42
    assert "line 42" in str(err)
    assert "unexpected key" in str(err)


def test_unknown_version_error_carries_version():
    err = ImportUnknownVersionError(found=2)
    assert err.found == 2
    assert "version 2" in str(err) or "found 2" in str(err)


def test_unknown_protocol_error_carries_protocol():
    err = ImportUnknownProtocolError(protocol="quantum")
    assert err.protocol == "quantum"
    assert "quantum" in str(err)


def test_unknown_backend_error_carries_kind():
    err = ImportUnknownBackendError(kind="bogus")
    assert err.kind == "bogus"
    assert "bogus" in str(err)


def test_display_name_collision_error_carries_name():
    err = ImportDisplayNameCollisionError(
        protocol="oauth2", display_name="spotify-marketing"
    )
    assert err.protocol == "oauth2"
    assert err.display_name == "spotify-marketing"
    assert "spotify-marketing" in str(err)


def test_env_var_missing_error_lists_missing():
    err = ImportEnvVarMissingError(
        var_name="SPOTIFY_PASSWORD",
        path="protocol_data.password",
    )
    assert err.var_name == "SPOTIFY_PASSWORD"
    assert err.path == "protocol_data.password"
    assert "SPOTIFY_PASSWORD" in str(err)


def test_id_collision_error_carries_id():
    err = ImportIdCollisionError(connection_id="conn-abc123")
    assert err.connection_id == "conn-abc123"
    assert "conn-abc123" in str(err)


def test_default_collision_error_carries_protocol():
    err = ImportDefaultCollisionError(
        protocol="oauth2", display_names=["a", "b"]
    )
    assert err.protocol == "oauth2"
    assert err.display_names == ["a", "b"]
    assert "oauth2" in str(err)


def test_master_key_mismatch_error_message():
    err = ImportMasterKeyMismatchError()
    assert "master key" in str(err).lower()


def test_protocol_data_invalid_error_carries_pydantic_message():
    """The message from Pydantic ValidationError should flow through."""
    err = ImportProtocolDataInvalidError(
        protocol="oauth2",
        display_name="spotify",
        message="provider field required",
    )
    assert "provider field required" in str(err)
    assert err.protocol == "oauth2"
    assert err.display_name == "spotify"
