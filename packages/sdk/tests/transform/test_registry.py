# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the transform operation registry."""

import pytest

from sagewai.transform.registry import TransformRegistry


async def _echo(content, *, project_id=None, **params):
    return f"echo:{content}"


def test_register_and_get():
    reg = TransformRegistry()
    reg.register("echo", _echo)
    assert reg.get("echo") is _echo
    assert "echo" in reg.names()


def test_get_unknown_raises_keyerror():
    reg = TransformRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_register_rejects_duplicate():
    reg = TransformRegistry()
    reg.register("echo", _echo)
    with pytest.raises(ValueError):
        reg.register("echo", _echo)


def test_default_registry_has_builtins():
    from sagewai.transform import default_registry

    assert set(default_registry().names()) >= {"graphify", "summarize"}


def test_public_exports():
    import sagewai.transform as transform

    for name in (
        "TransformEngine",
        "TransformRegistry",
        "TransformRequest",
        "TransformResult",
        "graphify",
        "summarize",
        "default_registry",
    ):
        assert hasattr(transform, name), f"missing public export: {name}"
