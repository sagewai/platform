# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for Sealed-i Pydantic models."""
import pytest

from sagewai.sealed.models import (
    EffectiveProfile,
    Profile,
    ProfileMetadata,
    ProfileWritePayload,
)


def test_profile_metadata_defaults():
    pm = ProfileMetadata(id="acme", name="Acme")
    assert pm.id == "acme"
    assert pm.name == "Acme"
    assert pm.description == ""
    assert pm.tags == []
    assert pm.last_rotated_at is None
    assert pm.allowed_workflows == []
    assert pm.env == {}
    assert pm.secret_keys == []


def test_profile_extends_metadata_with_secrets():
    p = Profile(
        id="acme", name="Acme",
        env={"DEBUG": "1"},
        secret_keys=["OPENAI_API_KEY"],
        secrets={"OPENAI_API_KEY": "sk-secret"},
    )
    assert p.secrets["OPENAI_API_KEY"] == "sk-secret"
    assert p.env["DEBUG"] == "1"


def test_profile_write_payload_requires_name():
    with pytest.raises(ValueError):
        ProfileWritePayload(name="")


def test_effective_profile_round_trip():
    e = EffectiveProfile(
        env={"OPENAI_API_KEY": "sk-..."},
        secret_keys={"OPENAI_API_KEY"},
        cascade_origins={"OPENAI_API_KEY": "system"},
    )
    assert "OPENAI_API_KEY" in e.env
    assert "OPENAI_API_KEY" in e.secret_keys
