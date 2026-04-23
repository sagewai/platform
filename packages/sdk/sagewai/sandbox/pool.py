# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SandboxPool — lifecycle owner for a worker's sandboxes.

Responsibilities:
- Translate SandboxMode into a Lifetime and keep-alive policy
- Cache per-run or per-worker handles
- Spawn a background reaper against the backend
- Expose acquire() as an async context manager used by the worker tool-dispatch loop
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from sagewai.sandbox.backend import SandboxBackend, SandboxHandle
from sagewai.sandbox.models import (
    SandboxConfig,
    SandboxLifetime,
    SandboxMode,
)
from sagewai.sandbox.secret_provider import EnvSecretProvider, SecretProvider

logger = logging.getLogger(__name__)


class _ProxyHandle:
    """Lazily-started handle for per_tool mode: a new container per exec()."""

    def __init__(self, spawner, factory_kwargs: dict) -> None:
        self._spawner = spawner
        self._kwargs = factory_kwargs
        self.mode = SandboxMode.PER_TOOL
        self.image = factory_kwargs["image"]
        self.image_digest = factory_kwargs.get("image_digest", "")
        self.sandbox_id = "proxy"

    async def exec(self, tool_call):
        handle = await self._spawner(**self._kwargs, lifetime=SandboxLifetime.PER_TOOL)
        try:
            return await handle.exec(tool_call)
        finally:
            await handle.stop()

    async def copy_in(self, src, dst): raise NotImplementedError
    async def copy_out(self, src, dst): raise NotImplementedError
    async def stats(self): return None
    async def stop(self, *, timeout: float = 10.0): return None


class SandboxPool:
    def __init__(
        self,
        *,
        backend: SandboxBackend,
        config: SandboxConfig,
        worker_id: str,
        scratch_root: Path,
        secret_provider: SecretProvider | None = None,
        reap_interval_s: float = 60.0,
        reap_older_than: timedelta = timedelta(minutes=10),
    ) -> None:
        self._backend = backend
        self._config = config
        self._worker_id = worker_id
        self._scratch_root = scratch_root
        self._secret_provider = secret_provider or EnvSecretProvider({})
        self._reap_interval_s = reap_interval_s
        self._reap_older_than = reap_older_than

        self._per_run_handles: dict[str, SandboxHandle] = {}
        self._worker_handle: SandboxHandle | None = None
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None

    @property
    def mode(self) -> SandboxMode:
        return self._config.mode or SandboxMode.NONE

    async def start_reaper(self) -> None:
        if self._reaper_task is not None:
            return
        self._reaper_task = asyncio.create_task(self._reap_loop(), name="sandbox-reaper")

    async def stop(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None
        async with self._lock:
            for handle in list(self._per_run_handles.values()):
                try:
                    await handle.stop()
                except Exception:
                    logger.debug("handle.stop() failed", exc_info=True)
            self._per_run_handles.clear()
            if self._worker_handle is not None:
                try:
                    await self._worker_handle.stop()
                except Exception:
                    logger.debug("worker handle stop failed", exc_info=True)
                self._worker_handle = None

    @asynccontextmanager
    async def acquire(
        self, *, project_id: str, run_id: str, image: str
    ) -> AsyncIterator[SandboxHandle]:
        mode = self.mode
        handle = await self._acquire_handle(
            mode=mode, project_id=project_id, run_id=run_id, image=image
        )
        try:
            yield handle
        finally:
            if mode is SandboxMode.NONE:
                # Null handle — dispose immediately.
                try:
                    await handle.stop()
                except Exception:
                    logger.debug("null handle stop failed", exc_info=True)
            elif mode is SandboxMode.PER_RUN:
                async with self._lock:
                    cached = self._per_run_handles.pop(run_id, None)
                if cached is not None:
                    try:
                        await cached.stop()
                    except Exception:
                        logger.debug("per_run stop failed", exc_info=True)
            # per_tool proxies are short-lived per exec; nothing to stop here.
            # per_worker handle is retained for the pool's lifetime (stop() cleans up).

    async def _acquire_handle(
        self, *, mode: SandboxMode, project_id: str, run_id: str, image: str
    ) -> SandboxHandle:
        factory_kwargs = await self._factory_kwargs(
            project_id=project_id, run_id=run_id, image=image
        )
        if mode is SandboxMode.NONE:
            return await self._backend.start(
                **factory_kwargs, lifetime=SandboxLifetime.PER_RUN
            )
        if mode is SandboxMode.PER_TOOL:
            return _ProxyHandle(self._backend.start, dict(factory_kwargs))
        if mode is SandboxMode.PER_RUN:
            async with self._lock:
                existing = self._per_run_handles.get(run_id)
                if existing is not None:
                    return existing
            handle = await self._backend.start(
                **factory_kwargs, lifetime=SandboxLifetime.PER_RUN
            )
            async with self._lock:
                self._per_run_handles[run_id] = handle
            return handle
        if mode is SandboxMode.PER_WORKER:
            async with self._lock:
                if self._worker_handle is not None:
                    return self._worker_handle
            handle = await self._backend.start(
                **factory_kwargs, lifetime=SandboxLifetime.PER_WORKER
            )
            async with self._lock:
                self._worker_handle = handle
            return handle
        raise ValueError(f"unknown sandbox mode: {mode!r}")

    async def _factory_kwargs(
        self, *, project_id: str, run_id: str, image: str
    ) -> dict:
        env = await self._secret_provider.env_for(
            project_id=project_id,
            run_id=run_id,
            agent_id=None,
            declared_scopes=[],
        )
        workdir = self._scratch_root / self._worker_id / "runs" / run_id
        workdir.mkdir(parents=True, exist_ok=True)
        return {
            "project_id": project_id,
            "run_id": run_id,
            "image": image,
            "image_digest": "",
            "env": dict(env),
            "network_policy": self._config.network_policy,
            "resource_limits": self._config.resource_limits,
            "workdir_mount": workdir,
        }

    async def _reap_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._reap_interval_s)
                killed = await self._backend.reap(older_than=self._reap_older_than)
                if killed:
                    logger.info("sandbox reaper killed %d orphan(s)", killed)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("reaper loop error")
