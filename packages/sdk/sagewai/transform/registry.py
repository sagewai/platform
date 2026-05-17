# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Registry of named transform operations."""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol


class TransformOp(Protocol):
    """An async transform operation.

    Takes resolved text and operation-specific keyword params, returns
    either a ``TransformResult`` or a bare string (wrapped by the engine).
    """

    def __call__(
        self, content: str, *, project_id: str | None = None, **params: object
    ) -> Awaitable: ...


class TransformRegistry:
    """Maps operation names to async transform callables."""

    def __init__(self) -> None:
        self._ops: dict[str, Callable] = {}

    def register(self, name: str, op: Callable) -> None:
        if name in self._ops:
            raise ValueError(f"transform operation already registered: {name}")
        self._ops[name] = op

    def get(self, name: str) -> Callable:
        if name not in self._ops:
            raise KeyError(f"unknown transform operation: {name}")
        return self._ops[name]

    def names(self) -> list[str]:
        return sorted(self._ops)
