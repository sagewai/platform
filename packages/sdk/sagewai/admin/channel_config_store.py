# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``ChannelConfigStore`` adapters over the two places the admin writes channel rows.

``build_decision_channels`` awaits ``list_channel_configs`` unguarded, inside every coordinator
tick, so neither adapter may raise: one unreadable row would stop the whole project's Tasks
rather than one channel. A row that cannot be decrypted or does not name a channel type is
skipped with a warning that carries its id and type and never its secret, and the resolver then
falls back to the console — the same fail-open-to-console shape it already uses for a channel
it cannot configure in ``build_decision_channels``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.notification_secrets import (
    NotificationSecretDecryptionError,
    decrypt_notification_secrets,
)
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenancy import ALL_SCOPES, MULTI_TENANT, RequestContext, UserRef

logger = logging.getLogger("sagewai.admin.channels")

_KIND = "notification_channel"


def _usable(row: Any) -> bool:
    """A row the resolver can read: a mapping that names its channel type."""
    if not isinstance(row, dict) or not str(row.get("channel_type") or row.get("type") or ""):
        logger.warning(
            "channel config is malformed and was skipped",
            extra={
                "event": "task.channel.malformed",
                "channel_id": row.get("id") if isinstance(row, dict) else None,
            },
        )
        return False
    return True


async def org_for_project(identity_store: IdentityStore, project_id: str) -> str | None:
    """The organization that owns ``project_id``, or None. Project ids are unique per org."""
    for org in await identity_store.list_orgs():
        if await identity_store.get_project(org["id"], project_id) is not None:
            return str(org["id"])
    return None


class AdminResourceChannelConfigStore:
    """Multi-tenant: the tenant-keyed rows the admin's channel CRUD writes.

    The coordinator has no request, so the adapter builds the read scope itself: one org-admin
    context per project id, which is no more authority than the loop already holds: it
    iterates every project of every organization. One adapter serves one organization, because
    a project's data key is read as ``get_project_data_key(org_id, project_id)``; a wrong
    organization finds no key, so the row is skipped rather than crossing a tenant.
    Org-shared rows carry no org tag in ``admin_resources`` and are deployment-wide in the
    one-org-per-deployment model. Only project-scoped rows are contained: the resource read
    filters by ``project_id``, and decrypt uses the project's data key.
    """

    def __init__(
        self,
        *,
        resource_store: AdminResourceStore,
        identity_store: IdentityStore,
        org_id: str,
    ) -> None:
        self._resource_store = resource_store
        self._identity_store = identity_store
        self._org_id = org_id

    async def list_channel_configs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """The project's own channels plus deployment-shared ones, secrets decrypted."""
        context = RequestContext(
            actor=UserRef(id="coordinator", label="task-coordinator"),
            org_id=self._org_id,
            project_id=project_id,
            roles=frozenset({"org:admin"}),
            scopes=ALL_SCOPES,
            request_id="",
            tenancy_mode=MULTI_TENANT,
        )
        configs: list[dict[str, Any]] = []
        for row in await self._resource_store.list_for(context, _KIND):
            if not _usable(row):
                continue
            try:
                configs.append(
                    await decrypt_notification_secrets(
                        row, identity_store=self._identity_store, org_id=self._org_id
                    )
                )
            except NotificationSecretDecryptionError:
                logger.warning(
                    "channel config could not be decrypted and was skipped",
                    extra={
                        "event": "task.channel.undecryptable",
                        "channel_id": row.get("id"),
                        "channel_type": row.get("channel_type") or row.get("type"),
                    },
                )
        return configs


class StateFileChannelConfigStore:
    """Single-org: the ``notification_channels`` rows the admin writes to the state file.

    Secrets are stored and used as they were given: single-org has no tenant-key model, which
    matches the notification channel state-file write path. This adapter only
    scopes. Read scope matches the file store's own rule: the project's rows plus every row
    with no project.
    """

    def __init__(self, *, state_file: AdminStateFile) -> None:
        self._state_file = state_file

    async def list_channel_configs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Read off the event loop: the state file is a blocking read inside a coordinator tick.

        The lifespan already reads the state-file project list this way.
        """
        data = await asyncio.to_thread(self._state_file._read)
        return [
            dict(row)
            for row in data.get("notification_channels", [])
            if _usable(row) and row.get("project_id") in (project_id, None, "")
        ]


__all__ = [
    "AdminResourceChannelConfigStore",
    "StateFileChannelConfigStore",
    "org_for_project",
]
