# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PoolKey shape: hashable, frozen, partitions by mode."""
from __future__ import annotations

import pytest

from sagewai.core.state import ExecutionMode
from sagewai.sandbox.models import NetworkPolicy, SandboxImageVariant, SandboxMode
from sagewai.sandbox.pool_protocol import PoolKey


def _key(**overrides) -> PoolKey:
    base = dict(
        image_digest="sha256:abc",
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.SANDBOXED,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )
    base.update(overrides)
    return PoolKey(**base)


def test_pool_key_is_hashable() -> None:
    key = _key()
    {key: 1}  # raises if not hashable


def test_pool_key_is_frozen() -> None:
    key = _key()
    with pytest.raises((AttributeError, TypeError)):
        key.image_digest = "sha256:zzz"  # type: ignore[misc]


def test_pool_key_equal_when_all_fields_equal() -> None:
    assert _key() == _key()


def test_pool_key_partitions_by_execution_mode() -> None:
    sandboxed = _key(execution_mode=ExecutionMode.SANDBOXED)
    full = _key(execution_mode=ExecutionMode.FULL)
    assert sandboxed != full
    assert hash(sandboxed) != hash(full)


def test_pool_key_partitions_by_image_digest() -> None:
    a = _key(image_digest="sha256:aaa")
    b = _key(image_digest="sha256:bbb")
    assert a != b


def test_pool_key_partitions_by_network_policy() -> None:
    a = _key(network_policy=NetworkPolicy.NONE)
    b = _key(network_policy=NetworkPolicy.FULL)
    assert a != b


def test_pool_key_partitions_by_image_variant() -> None:
    a = _key(image_variant=SandboxImageVariant.BASE)
    b = _key(image_variant=SandboxImageVariant.GENERAL)
    assert a != b
