# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SagewaiLLMClient — async HTTP client for the hosted blueprint service.

Owns:
- the base URL of the hosted service,
- the current :class:`InstanceIdentity`,
- a :class:`BlueprintCache` for graceful degradation,
- a long-lived :class:`httpx.AsyncClient`.

Each endpoint method returns a typed response model from
:mod:`.types`. The latest parsed :class:`QuotaStatus` is exposed as
:attr:`last_quota` after every response for callers that want to
surface the "N of M remaining" banner.

Graceful-degradation semantics (429 / 503 / network error) live in
Task 8. This module implements only the happy path and the raw HTTP
machinery.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any

import httpx

from .cache import BlueprintCache
from .errors import ClientUnreachable, QuotaExceeded, ServiceError
from .identity import InstanceIdentity, InstanceIdentityStore
from .quota import QUOTA_HEADER, QuotaStatus, parse_quota_header
from .signing import build_signed_headers
from .types import (
    FeedResponse,
    GenerateBlueprintRequest,
    GenerateBlueprintResponse,
    PublishBlueprintRequest,
    PublishBlueprintResponse,
    QuotaResponse,
    RetrieveBlueprintsRequest,
    RetrieveBlueprintsResponse,
    RunEvalRequest,
    RunEvalResponse,
    TelemetryEvent,
)

DEFAULT_BASE_URL = "https://sw-autopilot-llm.sagewai.ai"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _default_base_url() -> str:
    """Return base URL from ``SAGEWAI_LLM_BASE_URL`` env var or the default."""
    return os.environ.get("SAGEWAI_LLM_BASE_URL", DEFAULT_BASE_URL)


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SagewaiLLMClient:
    """Async HTTP client for the hosted Sagewai LLM blueprint service."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        identity: InstanceIdentity,
        cache: BlueprintCache,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
        identity_store: InstanceIdentityStore | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else _default_base_url()).rstrip("/")
        self.identity = identity
        self.cache = cache
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_http = http_client is None
        # Persists the server-issued secret after enrollment so later clients
        # skip the handshake. Optional — without it, enrollment runs once per
        # client instance (still correct, just an extra round trip).
        self._identity_store = identity_store
        self.last_quota: QuotaStatus | None = None
        self.last_degraded: bool = False

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> SagewaiLLMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ── HTTP plumbing ─────────────────────────────────────────────

    async def _ensure_enrolled(self) -> None:
        """Fetch the server-derived HMAC secret before any signed request.

        A freshly generated identity carries a placeholder random secret that
        can never validate against the server (which derives the per-instance
        secret as ``HKDF(master, instance_id)``). Enrollment exchanges the
        instance id for that derived secret exactly once.

        Tolerant by design: a 404 means an older server without the endpoint
        (dev-mode servers ignore the signature, so the placeholder still works);
        a 409 means the id was already claimed and the secret is unrecoverable.
        In both cases we mark ``registered`` so we don't retry every request.
        """
        if self.identity.registered:
            return
        url = f"{self.base_url}/v1/instances/enroll"
        try:
            resp = await self._http.post(
                url, json={"instance_id": self.identity.instance_id}
            )
        except httpx.HTTPError as exc:
            raise ClientUnreachable(f"POST /v1/instances/enroll: {exc}") from exc

        if resp.status_code == 200:
            self._adopt_secret(resp.json()["instance_secret"])
        elif resp.status_code in (404, 409):
            self._adopt_secret(self.identity.instance_secret)
        else:
            raise ServiceError(status_code=resp.status_code, body=resp.text)

    def _adopt_secret(self, secret: str) -> None:
        """Replace the identity's secret, mark it registered, and persist."""
        self.identity = InstanceIdentity(
            instance_id=self.identity.instance_id,
            instance_secret=secret,
            registered=True,
        )
        if self._identity_store is not None:
            self._identity_store.save(self.identity)

    async def _request(
        self,
        *,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        await self._ensure_enrolled()
        body = b"" if json_body is None else self._encode(json_body)
        timestamp = _now_iso()
        headers = build_signed_headers(
            instance_id=self.identity.instance_id,
            secret=self.identity.instance_secret,
            timestamp=timestamp,
            method=method,
            path=path,
            body=body,
        )
        headers["Content-Type"] = "application/json"
        url = f"{self.base_url}{path}"
        try:
            response = await self._http.request(
                method=method,
                url=url,
                headers=headers,
                content=body if body else None,
            )
        except httpx.HTTPError as exc:
            raise ClientUnreachable(f"{method} {path}: {exc}") from exc

        self.last_quota = parse_quota_header(response.headers.get(QUOTA_HEADER))

        if response.status_code == 429:
            q = self.last_quota
            raise QuotaExceeded(
                tier=q.tier if q else "unknown",
                limit=q.limit if q else 0,
                endpoint=q.endpoint if q else path,
            )
        if response.status_code >= 400:
            raise ServiceError(status_code=response.status_code, body=response.text)
        return response

    @staticmethod
    def _sanitize_cache_key(raw: str) -> str:
        """Map arbitrary strings to the cache's safe-key alphabet."""
        return re.sub(r"[^A-Za-z0-9._-]", "_", raw)

    @staticmethod
    def _encode(body: dict[str, Any]) -> bytes:
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    # ── Endpoint methods ──────────────────────────────────────────

    async def generate_blueprint(
        self, *, goal: str, context: dict[str, Any] | None = None
    ) -> GenerateBlueprintResponse:
        req = GenerateBlueprintRequest(goal=goal, context=context or {})
        response = await self._request(
            method="POST",
            path="/v1/blueprints/generate",
            json_body=req.model_dump(),
        )
        return GenerateBlueprintResponse.model_validate(response.json())

    async def retrieve_blueprints(self, *, goal: str, k: int = 5) -> RetrieveBlueprintsResponse:
        cache_key = "retrieve_" + self._sanitize_cache_key(goal)
        req = RetrieveBlueprintsRequest(goal=goal, k=k)
        try:
            response = await self._request(
                method="POST",
                path="/v1/blueprints/retrieve",
                json_body=req.model_dump(),
            )
        except (QuotaExceeded, ClientUnreachable):
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.last_degraded = True
                return RetrieveBlueprintsResponse.model_validate_json(cached)
            raise

        self.last_degraded = False
        body = response.text
        self.cache.put(cache_key, body)
        return RetrieveBlueprintsResponse.model_validate_json(body)

    async def publish_blueprint(
        self, *, blueprint_json: str, notes: str | None = None
    ) -> PublishBlueprintResponse:
        req = PublishBlueprintRequest(blueprint_json=blueprint_json, notes=notes)
        response = await self._request(
            method="POST",
            path="/v1/blueprints/publish",
            json_body=req.model_dump(),
        )
        return PublishBlueprintResponse.model_validate(response.json())

    async def get_feed(self, *, since: str) -> FeedResponse:
        cache_key = "feed_" + self._sanitize_cache_key(since)
        try:
            response = await self._request(
                method="GET",
                path=f"/v1/feed?since={since}",
                json_body=None,
            )
        except (QuotaExceeded, ClientUnreachable):
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.last_degraded = True
                return FeedResponse.model_validate_json(cached)
            raise

        self.last_degraded = False
        body = response.text
        self.cache.put(cache_key, body)
        return FeedResponse.model_validate_json(body)

    async def submit_telemetry(self, *, type_: str, payload: dict[str, Any] | None = None) -> None:
        event = TelemetryEvent(type=type_, payload=payload or {})
        await self._request(
            method="POST",
            path="/v1/telemetry",
            json_body=event.model_dump(),
        )

    async def run_eval(self, *, blueprint_json: str, dataset_id: str) -> RunEvalResponse:
        req = RunEvalRequest(blueprint_json=blueprint_json, dataset_id=dataset_id)
        response = await self._request(
            method="POST",
            path="/v1/eval/run",
            json_body=req.model_dump(),
        )
        return RunEvalResponse.model_validate(response.json())

    async def get_quota(self) -> QuotaResponse:
        response = await self._request(
            method="GET",
            path="/v1/quota",
            json_body=None,
        )
        return QuotaResponse.model_validate(response.json())
