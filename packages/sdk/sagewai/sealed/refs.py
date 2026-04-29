# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ProfileRef parser + backend registry."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sagewai.sealed.backend import ProfileBackend

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+\-.]*)://(.+)$")

BUILTIN_SCHEME = "builtin"

_DEFAULT_SCHEME = BUILTIN_SCHEME


def set_default_scheme(scheme: str) -> None:
    """Override the scheme used for bare-ID refs (no explicit `scheme://` prefix).

    URI-form refs are never affected — `builtin://x` always means builtin
    regardless of the default. Operators flip this to make `vault://` the
    de-facto default for a Vault-backed deployment.
    """
    if not _SCHEME_RE.match(f"{scheme}://x"):
        raise ValueError(
            f"invalid scheme {scheme!r}: must match [a-z][a-z0-9+\\-.]*"
        )
    global _DEFAULT_SCHEME
    _DEFAULT_SCHEME = scheme


class UnknownBackendError(LookupError):
    """Raised when a profile reference uses an unregistered scheme."""


@dataclass(frozen=True)
class ProfileRef:
    """A profile reference. Encodes which backend + which profile id."""

    scheme: str
    path: str

    @classmethod
    def parse(cls, ref: str) -> ProfileRef:
        """Parse 'scheme://path' or bare 'id' (defaults to set_default_scheme)."""
        match = _SCHEME_RE.match(ref)
        if match:
            return cls(scheme=match.group(1), path=match.group(2))
        return cls(scheme=_DEFAULT_SCHEME, path=ref)

    def __str__(self) -> str:
        """Return canonical URI form: 'scheme://path'.

        Always uses explicit scheme; bare IDs parsed via ProfileRef.parse('id')
        serialise as 'builtin://id', not 'id'. The round-trip is intentional —
        the canonical form has no implicit scheme.
        """
        return f"{self.scheme}://{self.path}"


_BACKENDS: dict[str, ProfileBackend] = {}


def register_backend(backend: ProfileBackend) -> None:
    """Register a backend by its scheme. Called at import time by each backend module.

    Idempotent: re-registering the same backend instance is a no-op.
    Raises ValueError if a DIFFERENT backend tries to claim a scheme already
    registered by another backend.
    """
    existing = _BACKENDS.get(backend.scheme)
    if existing is not None and existing is not backend:
        raise ValueError(
            f"scheme {backend.scheme!r} is already registered by {existing.name!r}; "
            f"cannot re-register with {backend.name!r}"
        )
    _BACKENDS[backend.scheme] = backend


def resolve_backend(ref: ProfileRef) -> ProfileBackend:
    """Look up the backend for this scheme. Raises UnknownBackendError."""
    backend = _BACKENDS.get(ref.scheme)
    if backend is None:
        raise UnknownBackendError(
            f"no backend registered for scheme {ref.scheme!r}; "
            f"available: {sorted(_BACKENDS.keys())}"
        )
    return backend


def list_registered_schemes() -> list[str]:
    """For admin UI / diagnostics."""
    return sorted(_BACKENDS.keys())
