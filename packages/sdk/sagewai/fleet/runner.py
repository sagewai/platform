# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""WorkerRunner — the `sagewai fleet run` worker-side daemon.

Registers against the gateway, heartbeats, long-poll claims tasks matching its
REGISTERED capabilities (the server derives them from the record), executes each
via an operator command (or a no-op echo), and reports the result with
transient-only retry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Default-DENY environment for `--exec` task subprocesses: a `--exec` task is
# untrusted code, so it inherits ONLY this minimal allowlist of system variables
# (enough to find binaries, resolve locale, and use a temp dir) plus the small
# SAGEWAI_TASK_* IDs injected per task. EVERYTHING else is dropped — including every
# ambient secret (SAGEWAI_ADMIN_TOKEN, SAGEWAI_MASTER_KEY, DATABASE_URL/
# SAGEWAI_DATABASE_URL, HMAC_MASTER_SECRET, provider API keys, …) — so task code
# can't reuse the daemon's credentials to call admin/fleet/data-plane APIs. An
# allowlist (not a denylist) is future-proof: new secrets are denied by default.
# Operators that need a specific value can set it inline in the --exec command.
_EXEC_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "PWD",
    }
)


class _PendingApproval(Exception):
    """Claim returned 403 status=pending — keep waiting for an admin to approve."""


class TerminalAuthError(Exception):
    """Claim returned a terminal auth error (rejected/revoked/404) — stop."""


class RegistrationError(RuntimeError):
    """Registration was rejected by the gateway (non-2xx). Carries the status
    so the CLI can surface a 401 token hint."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"register failed: {status_code} {detail}")


@dataclass
class WorkerRunner:
    base_url: str
    token: str = ""
    project: str | None = None
    name: str = "worker"
    models: list[str] = field(default_factory=list)
    pool: str = "default"
    labels: dict[str, str] = field(default_factory=dict)
    max_concurrent: int = 1
    enrollment_key: str | None = None
    worker_id: str | None = None
    exec_cmd: str | None = None
    exec_timeout: float = 300.0
    poll_timeout: float = 30.0
    heartbeat_interval: float = 10.0
    grace: float = 10.0
    http_client: httpx.AsyncClient | None = None
    _owned_client: httpx.AsyncClient | None = field(default=None, repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    # -- HTTP plumbing --------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.project:
            h["X-Project-ID"] = self.project
        return h

    def _client(self) -> httpx.AsyncClient:
        if self.http_client is not None:
            return self.http_client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=httpx.Timeout(self.poll_timeout + self.grace),
            )
        return self._owned_client

    async def aclose(self) -> None:
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    # -- lifecycle ------------------------------------------------------
    async def register(self) -> tuple[str, str]:
        r = await self._client().post(
            "/api/v1/fleet/register",
            json={
                "name": self.name,
                "models": self.models,
                "pool": self.pool,
                "labels": self.labels,
                "max_concurrent": self.max_concurrent,
                "enrollment_key": self.enrollment_key,
            },
        )
        if r.status_code not in (200, 201):
            raise RegistrationError(r.status_code, r.text[:200])
        data = r.json()
        self.worker_id = data["worker_id"]
        return data["worker_id"], data.get("status", "pending")

    async def _claim(self) -> dict | None:
        r = await self._client().post(
            "/api/v1/fleet/claim",
            json={"worker_id": self.worker_id, "poll_timeout": self.poll_timeout},
        )
        if r.status_code == 200 and r.content:
            return r.json()
        if r.status_code == 204:
            return None
        if r.status_code == 403:
            status = (r.json() or {}).get("status")
            if status == "pending":
                raise _PendingApproval()
            raise TerminalAuthError(f"worker {status or 'not approved'}")
        if r.status_code == 404:
            raise TerminalAuthError("worker unknown / out of scope")
        if r.status_code == 401:
            raise TerminalAuthError(
                "unauthorized (401) — set SAGEWAI_ADMIN_TOKEN for this gateway"
            )
        logger.warning("claim transient %s: %s", r.status_code, r.text[:200])
        return None

    async def _execute(self, task: dict) -> tuple[str, str | None, str | None]:
        if not self.exec_cmd:
            logger.info("echo-exec run_id=%s", task.get("run_id"))
            return "completed", f"echo: {task.get('run_id')}", None
        env = {k: v for k, v in os.environ.items() if k in _EXEC_ENV_ALLOWLIST}
        env.update(
            {
                "SAGEWAI_TASK_RUN_ID": str(task.get("run_id", "")),
                "SAGEWAI_TASK_JOB_ID": str(task.get("job_id", "")),
                "SAGEWAI_TASK_MODEL": str(task.get("model", "")),
                "SAGEWAI_TASK_POOL": str(task.get("pool", "")),
            }
        )
        proc = await asyncio.create_subprocess_shell(
            self.exec_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps(task).encode()), timeout=self.exec_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()  # reap the killed child — no zombie
            return "failed", None, "timeout"
        if proc.returncode == 0:
            return "completed", out.decode(errors="replace")[:4000], None
        return "failed", None, err.decode(errors="replace")[:4000]

    async def _report(self, run_id: str, status: str, output, error) -> bool:
        """POST the report with transient-only retry. Returns True iff the
        gateway accepted it (2xx); False on terminal auth or retry exhaustion."""
        delay = 0.5
        for attempt in range(5):
            try:
                r = await self._client().post(
                    "/api/v1/fleet/report",
                    json={
                        "worker_id": self.worker_id,
                        "run_id": run_id,
                        "status": status,
                        "output": output,
                        "error": error,
                    },
                )
            except httpx.HTTPError as exc:
                logger.warning("report network error (try %d): %s", attempt + 1, exc)
                await self._sleep_or_stop(delay)
                delay *= 2
                continue
            if r.status_code < 300:
                return True
            if r.status_code in (401, 403, 404):
                logger.error("report auth %s for run %s — not retrying: %s",
                             r.status_code, run_id, r.text[:200])
                return False
            if 500 <= r.status_code < 600:
                logger.warning("report 5xx (try %d): %s", attempt + 1, r.status_code)
                await self._sleep_or_stop(delay)
                delay *= 2
                continue
            logger.error("report failed %s: %s", r.status_code, r.text[:200])
            return False
        logger.error("report giving up for run %s", run_id)
        return False

    async def _heartbeat_once(self) -> None:
        try:
            await self._client().post(
                "/api/v1/fleet/heartbeat", json={"worker_id": self.worker_id}
            )
        except httpx.HTTPError as exc:
            logger.warning("heartbeat error: %s", exc)

    # -- drivers --------------------------------------------------------
    async def run_once(self) -> dict:
        """Register (if needed), claim one task, execute, report. Returns a
        structured result; never raises for pending/terminal claim states.

        Result shapes:
          {"claimed": False, "reason": "pending"|"no_task"}
          {"claimed": False, "reason": "terminal", "detail": "..."}   # rejected/revoked/unknown
          {"claimed": True, "run_id": ..., "status": ..., "reported": bool}
        """
        if self.worker_id is None:
            await self.register()
        try:
            task = await self._claim()
        except _PendingApproval:
            return {"claimed": False, "reason": "pending"}
        except TerminalAuthError as exc:
            return {"claimed": False, "reason": "terminal", "detail": str(exc)}
        if task is None:
            return {"claimed": False, "reason": "no_task"}
        status, output, error = await self._execute(task)
        reported = await self._report(task["run_id"], status, output, error)
        return {
            "claimed": True,
            "run_id": task["run_id"],
            "status": status,
            "reported": reported,
        }

    async def run(self) -> None:
        if self.worker_id is None:
            wid, status = await self.register()
            if status == "pending":
                logger.warning(
                    "worker %s PENDING — approve it in the Workers screen "
                    "or pass --enrollment-key",
                    wid,
                )
        self._install_signal_handlers()
        sem = asyncio.Semaphore(self.max_concurrent)
        hb = asyncio.create_task(self._heartbeat_loop())
        inflight: set[asyncio.Task] = set()
        terminal: TerminalAuthError | None = None
        try:
            while not self._stop.is_set():
                await sem.acquire()
                if self._stop.is_set():
                    sem.release()
                    break
                try:
                    task = await self._claim()
                except _PendingApproval:
                    sem.release()
                    await self._sleep_or_stop(self.heartbeat_interval)
                    continue
                except TerminalAuthError as exc:
                    sem.release()
                    logger.error("terminal: %s — exiting", exc)
                    terminal = exc
                    break
                if task is None:
                    sem.release()
                    await self._sleep_or_stop(1.0)
                    continue
                t = asyncio.create_task(self._handle(task, sem))
                inflight.add(t)
                t.add_done_callback(inflight.discard)
        finally:
            hb.cancel()
            # Await the cancelled heartbeat + any in-flight handlers so nothing
            # is left un-awaited (avoids "task was destroyed" warnings / zombies).
            await asyncio.gather(hb, *inflight, return_exceptions=True)
            await self.aclose()
        # Terminal auth (rejected/revoked/unknown) propagates AFTER draining so
        # the CLI can exit non-zero.
        if terminal is not None:
            raise terminal

    async def _handle(self, task: dict, sem: asyncio.Semaphore) -> None:
        try:
            status, output, error = await self._execute(task)
            reported = await self._report(task["run_id"], status, output, error)
            if not reported:
                logger.error(
                    "run %s executed (%s) but report was NOT accepted — "
                    "task may be stranded server-side",
                    task["run_id"], status,
                )
        finally:
            sem.release()

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            await self._heartbeat_once()
            await self._sleep_or_stop(self.heartbeat_interval)

    async def _sleep_or_stop(self, secs: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=secs)
        except asyncio.TimeoutError:
            pass

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):  # pragma: no cover
                pass

    def stop(self) -> None:
        self._stop.set()
