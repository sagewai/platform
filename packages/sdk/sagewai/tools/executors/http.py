# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``kind: http`` executor — REST/GraphQL SaaS APIs."""
from __future__ import annotations

import string
from typing import Any, Callable

import httpx
from jsonschema import Draft202012Validator

from sagewai.tools.registry import CatalogEntry


class InputValidationError(ValueError):
    pass


class OutputValidationError(ValueError):
    pass


class UnknownOperationError(KeyError):
    pass


class AuthConfigurationError(RuntimeError):
    pass


def _validate(schema: dict | None, payload: Any, err_cls: type[Exception]) -> None:
    if not schema:
        return
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        raise err_cls("; ".join(e.message for e in errors))


def _format_path(template: str, inputs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Substitute ``{name}`` segments; remaining inputs become query/body params."""
    placeholders = {fname for _, fname, _, _ in string.Formatter().parse(template) if fname}
    missing = placeholders - inputs.keys()
    if missing:
        raise InputValidationError(f"missing path params: {sorted(missing)}")
    path = template.format(**{k: inputs[k] for k in placeholders})
    extras = {k: v for k, v in inputs.items() if k not in placeholders}
    return path, extras


def _build_auth_headers(auth_cfg: dict, creds: dict[str, str]) -> dict[str, str]:
    kind = auth_cfg["kind"]
    if kind == "none":
        return {}
    if kind in ("api_key", "bearer"):
        token = next((v for v in creds.values() if v), None)
        if not token:
            raise AuthConfigurationError("missing credential")
        return {auth_cfg.get("header", "Authorization"): f"{auth_cfg.get('prefix', '')}{token}"}
    if kind == "basic":
        import base64
        user = creds.get("USERNAME") or ""
        pw = creds.get("PASSWORD") or ""
        encoded = base64.b64encode(f"{user}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    raise AuthConfigurationError(f"auth.kind {kind!r} not yet supported by this executor")


async def run(
    entry: CatalogEntry,
    *,
    operation: str | None,
    inputs: dict[str, Any],
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    http_cfg = entry.exec_["http"]
    if operation not in http_cfg["operations"]:
        raise UnknownOperationError(operation)
    op = http_cfg["operations"][operation]

    _validate(op.get("input_schema"), inputs, InputValidationError)
    path, extras = _format_path(op["path"], inputs)
    creds = get_credentials(project_id=project_id, kind="tool", id=entry.id)
    headers = _build_auth_headers(http_cfg["auth"], creds)

    url = http_cfg["base_url"].rstrip("/") + path
    method = op["method"].upper()
    body_format = op.get("body_format", "json")
    async with httpx.AsyncClient() as client:
        if method == "GET":
            resp = await client.get(url, headers=headers, params=extras or None)
        elif body_format == "form":
            resp = await client.request(method, url, headers=headers, data=extras or None)
        else:
            resp = await client.request(method, url, headers=headers, json=extras or None)
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    _validate(op.get("output_schema"), payload, OutputValidationError)
    return payload
