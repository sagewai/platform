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
    ep = entry.exec_["sdk"]["entrypoint"]
    fn = _resolve(ep)
    return await fn(inputs)
