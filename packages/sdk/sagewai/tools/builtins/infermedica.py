# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Infermedica tool — clinical symptom checker.

Infermedica requires both ``App-Id`` and ``App-Key`` headers on every
request — ships as ``kind: sdk``. Carries the ``medical.advisory``
scope in addition to ``health.read``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_BASE = "https://api.infermedica.com/v3"
_OPS = {
    "parse_symptoms": "/parse",
    "diagnose": "/diagnosis",
    "suggest": "/suggest",
    "triage": "/triage",
}


async def infermedica(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Dispatch an Infermedica v3 API operation (all POST)."""
    op = payload.get("_operation")
    if op not in _OPS:
        raise ValueError(
            f"unknown operation: {op!r}; expected one of {list(_OPS)}"
        )

    creds = get_credentials(project_id=project_id, kind="tool", id="infermedica_api")
    app_id = creds.get("INFERMEDICA_APP_ID")
    app_key = creds.get("INFERMEDICA_APP_KEY")
    if not app_id:
        raise RuntimeError("infermedica: missing INFERMEDICA_APP_ID credential")
    if not app_key:
        raise RuntimeError("infermedica: missing INFERMEDICA_APP_KEY credential")

    headers = {
        "App-Id": app_id,
        "App-Key": app_key,
        "Content-Type": "application/json",
    }

    if op == "parse_symptoms":
        body = {"text": payload["text"], "age": payload["age"]}
    else:
        body = {
            "sex": payload["sex"],
            "age": payload["age"],
            "evidence": payload["evidence"],
        }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_BASE}{_OPS[op]}", headers=headers, json=body
        )
    resp.raise_for_status()
    return resp.json()
