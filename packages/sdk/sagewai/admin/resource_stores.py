# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The bundle of Postgres-backed tenant resource stores (multi-tenant mode).

In multi-tenant mode the admin app routes tenant-scoped resources (providers,
agents, connections) through ctx-scoped Postgres stores instead of the
file-backed ``AdminStateFile``. :class:`ResourceStores` is the single seam the
app hangs on ``app.state`` so route handlers can reach the active store; in
single-org mode it is ``None`` and the routes keep their unchanged ``sf.*`` path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sagewai.admin.tenancy import is_multi_tenant


@dataclass
class ResourceStores:
    provider: Any = None
    agent: Any = None
    connection: Any = None
    run: Any = None
    prompt_log: Any = None
    admin_resource: Any = None
    api_token: Any = None


async def build_resource_stores(identity_store: Any) -> ResourceStores | None:
    """Construct the Postgres-backed tenant stores in multi-tenant mode (else None).

    Provider, agent, connection, run, and prompt-log stores are built here so
    multi-tenant routes never drift back to file-backed state.
    """
    if not is_multi_tenant():
        return None
    from sagewai.admin.provider_store import PostgresProviderStore
    from sagewai.db import factory

    engine = factory.get_engine()
    # The provider store needs an IdentityStore to resolve per-project data keys
    # for secret encryption. In the default ``create_admin_serve_app(sf)`` path
    # no store is injected (AuthMiddleware builds its own lazily), so fall back to
    # one on the same process engine — otherwise encrypting a project provider
    # secret would dereference ``None`` (no get_project_data_key).
    if identity_store is None:
        from sagewai.admin.identity_store import IdentityStore

        identity_store = IdentityStore(engine=engine)
    provider = PostgresProviderStore(engine=engine, identity_store=identity_store)
    await provider.init()
    from sagewai.admin.tenant_agent_store import PostgresTenantAgentStore

    agent = PostgresTenantAgentStore(engine=engine)
    await agent.init()
    from sagewai.connections.postgres_store import PostgresConnectionStore
    from sagewai.connections.protocols import DEFAULT_KEY_FOR, PROTOCOLS

    connection = PostgresConnectionStore(
        engine=engine,
        allowed_protocols=tuple(p.id for p in PROTOCOLS),
        default_key_for=DEFAULT_KEY_FOR,
    )
    await connection.init()
    from sagewai.admin.store import RunStore
    from sagewai.observability.prompt_store import PromptStore

    run = RunStore(engine=engine)
    await run.init()
    prompt_log = PromptStore(engine=engine)
    await prompt_log.init()
    from sagewai.admin.admin_resource_store import AdminResourceStore

    admin_resource = AdminResourceStore(engine=engine)
    await admin_resource.init()
    from sagewai.admin.api_token_store import ApiTokenStore

    api_token = ApiTokenStore(engine=engine)
    await api_token.init()
    return ResourceStores(
        provider=provider,
        agent=agent,
        connection=connection,
        run=run,
        prompt_log=prompt_log,
        admin_resource=admin_resource,
        api_token=api_token,
    )
