# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Error hierarchy for connection import/export.

Each error class carries a stable ``code`` ClassVar that matches the spec's
error-codes table. The codes are the contract for tooling that consumes
the JSON-shaped result of an import operation.
"""
from __future__ import annotations

from typing import ClassVar


class ImportError(Exception):
    """Base for all import/export errors."""

    code: ClassVar[str] = "import_error"


class ImportYamlParseError(ImportError):
    """The YAML document didn't parse."""

    code: ClassVar[str] = "import_yaml_parse_error"

    def __init__(self, *, line: int | None = None, message: str) -> None:
        self.line = line
        loc = f"line {line}: " if line is not None else ""
        super().__init__(f"{loc}{message}")


class ImportUnknownVersionError(ImportError):
    """The top-level ``version`` field is missing or not supported."""

    code: ClassVar[str] = "import_unknown_version"

    def __init__(self, *, found: int | None) -> None:
        self.found = found
        super().__init__(
            f"unsupported export format version (found {found!r}; only 1 is supported)"
        )


class ImportUnknownProtocolError(ImportError):
    """A connection row references a protocol not in ``PROTOCOLS``."""

    code: ClassVar[str] = "import_unknown_protocol"

    def __init__(self, *, protocol: str) -> None:
        self.protocol = protocol
        super().__init__(f"unknown protocol: {protocol!r}")


class ImportProtocolDataInvalidError(ImportError):
    """The plugin's ``protocol_data_schema()`` rejected the row."""

    code: ClassVar[str] = "import_protocol_data_invalid"

    def __init__(self, *, protocol: str, display_name: str, message: str) -> None:
        self.protocol = protocol
        self.display_name = display_name
        super().__init__(
            f"{protocol}:{display_name}: protocol_data validation failed: {message}"
        )


class ImportUnknownBackendError(ImportError):
    """A connection row references a credentials backend not in ``BACKENDS``."""

    code: ClassVar[str] = "import_unknown_backend"

    def __init__(self, *, kind: str) -> None:
        self.kind = kind
        super().__init__(f"unknown credentials backend: {kind!r}")


class ImportDisplayNameCollisionError(ImportError):
    """In create-only mode: a ``display_name`` already exists in the target."""

    code: ClassVar[str] = "import_display_name_collision"

    def __init__(self, *, protocol: str, display_name: str) -> None:
        self.protocol = protocol
        self.display_name = display_name
        super().__init__(
            f"{protocol}:{display_name}: display_name already exists in target"
        )


class ImportEnvVarMissingError(ImportError):
    """In placeholder mode: a ``${VAR}`` reference couldn't be resolved."""

    code: ClassVar[str] = "import_env_var_missing"

    def __init__(self, *, var_name: str, path: str) -> None:
        self.var_name = var_name
        self.path = path
        super().__init__(
            f"environment variable {var_name!r} (referenced at {path}) is not set"
        )


class ImportIdCollisionError(ImportError):
    """In preserve-ids mode: the imported ``id`` already exists in the target."""

    code: ClassVar[str] = "import_id_collision"

    def __init__(self, *, connection_id: str) -> None:
        self.connection_id = connection_id
        super().__init__(f"connection id {connection_id!r} already exists in target")


class ImportDefaultCollisionError(ImportError):
    """Two import rows both have ``is_default: true`` for the same group."""

    code: ClassVar[str] = "import_default_collision"

    def __init__(self, *, protocol: str, display_names: list[str]) -> None:
        self.protocol = protocol
        self.display_names = display_names
        super().__init__(
            f"{protocol}: multiple is_default=true rows ({display_names!r})"
        )


class ImportMasterKeyMismatchError(ImportError):
    """In encrypted mode: a sample decrypt failed against the target's master key."""

    code: ClassVar[str] = "import_master_key_mismatch"

    def __init__(self) -> None:
        super().__init__(
            "sample decrypt failed — target environment doesn't share the source "
            "master key or backend access; cannot import encrypted-mode YAML"
        )


__all__ = [
    "ImportError",
    "ImportYamlParseError",
    "ImportUnknownVersionError",
    "ImportUnknownProtocolError",
    "ImportProtocolDataInvalidError",
    "ImportUnknownBackendError",
    "ImportDisplayNameCollisionError",
    "ImportEnvVarMissingError",
    "ImportIdCollisionError",
    "ImportDefaultCollisionError",
    "ImportMasterKeyMismatchError",
]
