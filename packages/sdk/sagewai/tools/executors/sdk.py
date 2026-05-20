# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``kind: sdk`` executor — calls an in-process Python entrypoint."""
from __future__ import annotations

import importlib
import inspect
from typing import Any, Awaitable, Callable

from sagewai.tools.registry import CatalogEntry


class EntrypointResolutionError(RuntimeError):
    pass


def _resolve(entrypoint: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    try:
        module_path, _, attr = entrypoint.partition(":")
        mod = importlib.import_module(module_path)
        return getattr(mod, attr)
    except (ImportError, AttributeError) as exc:
        raise EntrypointResolutionError(entrypoint) from exc


async def run(
    entry: CatalogEntry,
    *,
    operation: str | None,
    inputs: dict[str, Any],
    project_id: str,
    get_credentials: Callable[..., Any],
) -> dict[str, Any]:
    """Invoke an in-process ``payload -> dict`` entrypoint.

    The contract has two flavours:

    1. ``async def fn(payload: dict) -> dict`` — batch-1 builtins
       (``diff_text``, ``fetch_url``, etc.). No credentials needed.

    2. ``async def fn(payload: dict, *, project_id: str, get_credentials: Callable) -> dict``
       — batch-2a builtins that talk to external SaaS and need creds
       (``email_send``, ``mailchimp_api``).

    We introspect the entrypoint's signature and pass the credential
    kwargs only if accepted, so both shapes work.
    """
    ep = entry.exec_["sdk"]["entrypoint"]
    fn = _resolve(ep)
    # Re-inject _operation into the payload for multi-op sdk builtins
    # (e.g. mailchimp_api dispatches add_subscriber vs send_campaign by
    # reading payload["_operation"]). The factory stripped it before
    # calling us. Single-op builtins ignore the extra key harmlessly.
    if operation is not None and isinstance(inputs, dict) and "_operation" not in inputs:
        inputs = {**inputs, "_operation": operation}
    sig = inspect.signature(fn)
    if "get_credentials" in sig.parameters:
        return await fn(inputs, project_id=project_id, get_credentials=get_credentials)
    return await fn(inputs)
