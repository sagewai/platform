# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ProfileBackend Protocol + error classes."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from sagewai.sealed.models import Profile, ProfileMetadata, ProfileWritePayload


class ProfileNotFoundError(LookupError):
    """Raised when a backend can't find the requested profile."""


class BackendUnsupportedOperationError(NotImplementedError):
    """Raised when a backend doesn't implement an optional method."""


@runtime_checkable
class ProfileBackend(Protocol):
    """Pluggable storage for security profiles.

    Sealed-i ships only BuiltinAdminStoreBackend.
    Sealed-ii adds 1Password, Bitwarden, Vault, AWS SM, SOPS adapters
    by registering instances against the global backend registry.
    """

    name: str
    scheme: str

    async def list_profiles(self) -> list[ProfileMetadata]:
        """Return all profiles' metadata (no decrypted secret values).

        Used by admin UI list page and CLI `sagewai admin profiles list`.
        """

    async def get_profile_metadata(self, profile_id: str) -> ProfileMetadata:
        """Return metadata for a single profile by id.

        Raises ProfileNotFoundError if the id is not present.
        """

    async def get_profile(self, profile_id: str) -> Profile:
        """Return the full profile, including decrypted secret values.

        Used only by reveal endpoint and cascade resolution at injection time.
        Backends should audit every call (Task 6).
        Raises ProfileNotFoundError if the id is not present.
        """

    async def save_profile(self, payload: ProfileWritePayload) -> Profile:
        """Create or update a profile from the given write payload.

        On create, payload.id is required. On update, payload.id may be None
        (the URL/CLI supplies the id externally). Returns the resulting Profile.
        """

    async def delete_profile(self, profile_id: str) -> None:
        """Permanently remove a profile.

        Raises ProfileNotFoundError if the id is not present.
        """

    async def supports_master_key_rotation(self) -> bool:
        """Return True if this backend can re-encrypt all secrets under a new key.

        Called before rotate_master_key() to gate the CLI rotation command.
        Backends that delegate key management to an external service
        (e.g. AWS KMS, 1Password, Vault transit engine) should return False;
        the upstream service handles rotation externally.
        """

    async def rotate_master_key(self, new_key: bytes) -> int:
        """Re-encrypt every stored secret value under new_key.

        Returns the count of individual secret values re-encrypted —
        NOT the count of profiles. Profiles with no secrets contribute 0.
        Callers use this count for progress and audit reporting only.

        Raises BackendUnsupportedOperationError if
        supports_master_key_rotation() returns False.
        """
