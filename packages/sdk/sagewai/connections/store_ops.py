# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Async/sync bridge for connection stores.

The original file-backed ``ConnectionStore`` is synchronous. Tenant stores use
``AsyncEngine``. Admin routes and protocol extra routes are already async, so
they call these helpers to support either implementation without splitting the
connection boundary again.
"""

from __future__ import annotations

import inspect
from typing import Any


async def call_store(method: Any, *args: Any, **kwargs: Any) -> Any:
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def store_list(store: Any, *args: Any, **kwargs: Any) -> Any:
    return await call_store(store.list, *args, **kwargs)


async def store_get(store: Any, *args: Any, **kwargs: Any) -> Any:
    return await call_store(store.get, *args, **kwargs)


async def store_create(store: Any, *args: Any, **kwargs: Any) -> Any:
    return await call_store(store.create, *args, **kwargs)


async def store_update(store: Any, *args: Any, **kwargs: Any) -> Any:
    return await call_store(store.update, *args, **kwargs)


async def store_delete(store: Any, *args: Any, **kwargs: Any) -> Any:
    return await call_store(store.delete, *args, **kwargs)


async def store_set_default(store: Any, *args: Any, **kwargs: Any) -> Any:
    return await call_store(store.set_default, *args, **kwargs)


async def store_update_test_result(store: Any, *args: Any, **kwargs: Any) -> Any:
    return await call_store(store.update_test_result, *args, **kwargs)


__all__ = [
    "call_store",
    "store_create",
    "store_delete",
    "store_get",
    "store_list",
    "store_set_default",
    "store_update",
    "store_update_test_result",
]
