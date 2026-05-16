# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""acl field round-trips on Profile / ProfileMetadata / ProfileWritePayload / EffectiveProfile."""
from __future__ import annotations

import pytest

from sagewai.sealed.models import (
    EffectiveProfile,
    Profile,
    ProfileMetadata,
    ProfileWritePayload,
)


def test_profile_metadata_default_acl_empty_dict() -> None:
    pm = ProfileMetadata(id="p1", name="P One")
    assert pm.acl == {}


def test_profile_carries_acl() -> None:
    p = Profile(
        id="p1", name="P One",
        secrets={"K": "v"},
        acl={"claude-code": ["K"], "shell": []},
    )
    assert p.acl == {"claude-code": ["K"], "shell": []}


def test_profile_write_payload_accepts_acl() -> None:
    payload = ProfileWritePayload(
        id="p1", name="P One",
        secrets={"K": "v"},
        acl={"codex": ["K"]},
    )
    assert payload.acl == {"codex": ["K"]}


def test_effective_profile_carries_acl() -> None:
    ep = EffectiveProfile(
        env={"K": "v"},
        secret_keys={"K"},
        cascade_origins={"K": "system"},
        acl={"shell": []},
    )
    assert ep.acl == {"shell": []}


def test_acl_field_optional_in_metadata_response() -> None:
    # Round-trip via dict to mimic JSON deserialisation
    raw = {"id": "p", "name": "P"}
    pm = ProfileMetadata(**raw)
    assert pm.acl == {}


def test_acl_strict_typing_rejects_non_list_values() -> None:
    with pytest.raises(Exception):
        ProfileWritePayload(
            id="p", name="P",
            acl={"claude-code": "ANTHROPIC_API_KEY"},  # type: ignore[arg-type]
        )
