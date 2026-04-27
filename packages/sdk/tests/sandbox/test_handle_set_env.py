# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SandboxHandle.set_env stores env in-memory and exec merges via --env."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_docker_handle_stores_env_after_set_env() -> None:
    from sagewai.sandbox.docker_backend import DockerSandboxHandle

    handle = DockerSandboxHandle(
        client=MagicMock(),
        container=MagicMock(_id="c-1"),
        image="img",
        image_digest="sha256:x",
        sandbox_id="s-1",
        docker_bin="docker",
    )
    await handle.set_env({"K1": "v1", "K2": "v2"})
    assert handle._exec_env == {"K1": "v1", "K2": "v2"}


@pytest.mark.asyncio
async def test_docker_exec_merges_stored_env_into_argv() -> None:
    from sagewai.sandbox.docker_backend import DockerSandboxHandle
    from sagewai.sandbox.models import ToolCall

    handle = DockerSandboxHandle(
        client=MagicMock(),
        container=MagicMock(_id="c-1"),
        image="img",
        image_digest="sha256:x",
        sandbox_id="s-1",
        docker_bin="docker",
    )
    await handle.set_env({"K": "v"})

    captured: list[str] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self, _payload):
            return (b'{"jsonrpc":"2.0","result":{"ok":true},"id":1}\n', b"")

        def kill(self): ...
        async def wait(self): ...

    async def _spy(*args, **kwargs):
        captured.extend(args)
        return _FakeProc()

    with patch("asyncio.create_subprocess_exec", side_effect=_spy):
        result = await handle.exec(ToolCall(tool="ping", args={}, call_id="c-1"))

    assert result.ok is True
    assert "--env" in captured
    assert "K=v" in captured


@pytest.mark.asyncio
async def test_null_handle_set_env_updates_env() -> None:
    from pathlib import Path

    from sagewai.sandbox.null_backend import NullSandboxHandle

    handle = NullSandboxHandle(
        sandbox_id="s-1",
        env={"K": "old"},
        workdir=Path("/tmp"),
    )
    await handle.set_env({"K": "new"})
    assert handle._env == {"K": "new"}
