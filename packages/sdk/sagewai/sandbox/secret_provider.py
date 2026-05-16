# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SecretProvider seam.

Real JIT-credentials / redaction / audit implementation is deferred to the
"Sagewai Sealed" spec. This module ships only the protocol and a minimal
env-var default so the sandbox has a stable integration point now.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretProvider(Protocol):
    """Returns the env-var bundle a sandbox should start with.

    Called once per sandbox start, never by the tool-runner at runtime.
    """

    async def env_for(
        self,
        *,
        project_id: str,
        run_id: str,
        agent_id: str | None,
        declared_scopes: list[str],
        **kwargs: object,
    ) -> Mapping[str, str]:
        ...


class EnvSecretProvider:
    """Minimal default — returns project-scoped entries from an in-memory store.

    Host environment variables are never returned; tenants only see secrets
    explicitly recorded in their project's entry.
    """

    def __init__(self, store: Mapping[str, Mapping[str, str]]) -> None:
        self._store = store

    async def env_for(
        self,
        *,
        project_id: str,
        run_id: str,
        agent_id: str | None,
        declared_scopes: list[str],
        **_kwargs: object,
    ) -> Mapping[str, str]:
        entries = self._store.get(project_id) or {}
        return dict(entries)
