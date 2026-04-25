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
        self._probed_digests: set[str] = set()

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
        self,
        *,
        project_id: str,
        run_id: str,
        image: str,
        # NEW in Sealed-i — propagated to _factory_kwargs → secret provider
        security_profile_ref: str | None = None,
        effective_env_keys: list[str] | None = None,
        effective_secret_keys: list[str] | None = None,
        workflow_name: str | None = None,
    ) -> AsyncIterator[SandboxHandle]:
        mode = self.mode
        handle = await self._acquire_handle(
            mode=mode,
            project_id=project_id,
            run_id=run_id,
            image=image,
            security_profile_ref=security_profile_ref,
            effective_env_keys=effective_env_keys,
            effective_secret_keys=effective_secret_keys,
            workflow_name=workflow_name,
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
        self,
        *,
        mode: SandboxMode,
        project_id: str,
        run_id: str,
        image: str,
        security_profile_ref: str | None = None,
        effective_env_keys: list[str] | None = None,
        effective_secret_keys: list[str] | None = None,
        workflow_name: str | None = None,
    ) -> SandboxHandle:
        factory_kwargs = await self._factory_kwargs(
            project_id=project_id,
            run_id=run_id,
            image=image,
            security_profile_ref=security_profile_ref,
            effective_env_keys=effective_env_keys,
            effective_secret_keys=effective_secret_keys,
            workflow_name=workflow_name,
        )
        maybe_probe = getattr(self._backend, "probe_runner", None)
        if mode is SandboxMode.NONE:
            return await self._backend.start(
                **factory_kwargs, lifetime=SandboxLifetime.PER_RUN
            )
        if mode is SandboxMode.PER_TOOL:
            # NOTE: probe caching by digest for PER_TOOL mode is a known gap —
            # _ProxyHandle creates and destroys containers per exec(), so there
            # is no persistent handle to probe against. This is a future
            # optimization.
            return _ProxyHandle(self._backend.start, dict(factory_kwargs))
        if mode is SandboxMode.PER_RUN:
            async with self._lock:
                existing = self._per_run_handles.get(run_id)
                if existing is not None:
                    return existing
            handle = await self._backend.start(
                **factory_kwargs, lifetime=SandboxLifetime.PER_RUN
            )
            if maybe_probe is not None and handle.image_digest not in self._probed_digests:
                await maybe_probe(handle)
                self._probed_digests.add(handle.image_digest)
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
            if maybe_probe is not None and handle.image_digest not in self._probed_digests:
                await maybe_probe(handle)
                self._probed_digests.add(handle.image_digest)
            async with self._lock:
                self._worker_handle = handle
            return handle
        raise ValueError(f"unknown sandbox mode: {mode!r}")

    async def _factory_kwargs(
        self,
        *,
        project_id: str,
        run_id: str,
        image: str,
        security_profile_ref: str | None = None,
        effective_env_keys: list[str] | None = None,
        effective_secret_keys: list[str] | None = None,
        workflow_name: str | None = None,
    ) -> dict:
        sealed_levels = self._build_sealed_levels(
            workflow_name=workflow_name,
            security_profile_ref=security_profile_ref,
        )
        env = await self._secret_provider.env_for(
            project_id=project_id,
            run_id=run_id,
            agent_id=None,
            declared_scopes=[],
            security_profile_ref=security_profile_ref,
            effective_env_keys=effective_env_keys,
            effective_secret_keys=effective_secret_keys,
            sealed_levels=sealed_levels,
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

    def _build_sealed_levels(
        self,
        *,
        workflow_name: str | None,
        security_profile_ref: str | None,
    ) -> list | None:
        """Re-build the sealed cascade at injection time.

        Returns None when nothing is configured (the secret provider then
        no-ops). Re-fetching admin-state at injection time means rotations
        between enqueue and start are surfaced via profile.drift_at_injection.
        """
        if not security_profile_ref and workflow_name is None:
            return None
        try:
            from sagewai.admin.state_file import AdminStateFile
            from sagewai.sealed.resolution import CascadeLevel
        except ImportError:
            return None

        try:
            state = AdminStateFile()
            sealed_cfg = state.get_sealed_config()
            workflow_cfg = (
                state.get_workflow_sealed_config(workflow_name) if workflow_name else None
            ) or {}
        except Exception:
            return None

        levels = [
            CascadeLevel(
                name="system",
                profile_ref=sealed_cfg.get("system_profile_ref"),
                overrides=sealed_cfg.get("system_overrides"),
            ),
            CascadeLevel(
                name="workflow",
                profile_ref=workflow_cfg.get("profile_ref"),
                overrides=workflow_cfg.get("overrides"),
            ),
            CascadeLevel(
                name="user",
                profile_ref=security_profile_ref,
                overrides=None,
            ),
        ]
        if not any(lv.profile_ref for lv in levels):
            return None
        return levels

    def advertised_labels(self) -> dict[str, str]:
        """Labels this pool contributes to worker registration.

        Keys are namespaced with ``sandbox.`` to avoid colliding with
        operator-defined labels.
        """
        return {
            "sandbox.mode": self._config.mode.value,
            "sandbox.backend": self._backend.name,
            "sandbox.image_variants": self._advertised_variants_csv(),
            "sandbox.network_policy": self._config.network_policy.value,
        }

    def _advertised_variants_csv(self) -> str:
        """CSV of image variants this worker accepts routing for.

        Default: every variant in image_manifest.PINNED_DIGESTS.
        Override: list[SandboxImageVariant] in SandboxConfig.image_variants
        for pre-warmed / restricted pools.
        """
        override = self._config.image_variants
        if override is not None:
            return ",".join(v.value for v in override)
        from sagewai.sandbox.image_manifest import PINNED_DIGESTS
        return ",".join(sorted(PINNED_DIGESTS.keys()))

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
