# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Generic CRUD admin routes for the Connections Platform.

Mounts 10 routes at ``/api/v1/admin/connections/`` that delegate to
protocol plugins via :func:`sagewai.connections.bootstrap.build_connections_context`.

Each plugin's :meth:`extra_routes` is additionally mounted under
``/api/v1/admin/connections/<plugin.id>/`` (e.g., oauth2's
``/start`` + ``/callback`` + ``/refresh`` + ``/revoke``).

Auth: every route except plugin extras (which set their own policy via
the plugin's contract) requires the ``sagewai_auth`` cookie via
:func:`sagewai.admin.autopilot_routes._require_auth`. Plugin
``extra_routes`` use a module-level context injection (set by
``register`` at app construction time) so handlers don't take an extra
parameter for the context.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sagewai.admin.state_file import AdminStateFile
from sagewai.connections.bootstrap import (
    ConnectionsContext,
    build_connections_context,
)
from sagewai.connections.credentials import all_backends
from sagewai.connections.errors import (
    ConnectionNotFoundError,
    DuplicateDisplayNameError,
    UnknownProtocolError,
)
from sagewai.connections.protocols import all_protocols, get_protocol


# ── Request bodies ──────────────────────────────────────────────────


class CreateConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str
    display_name: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list)
    credentials_backend: dict[str, Any] | None = None
    protocol_data: dict[str, Any]


class PatchConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    tags: list[str] | None = None
    credentials_backend: dict[str, Any] | None = None
    protocol_data: dict[str, Any] | None = None


# ── Helpers ─────────────────────────────────────────────────────────


def _project_scope(request: Request) -> str | None:
    pid = (
        request.headers.get("x-project-id")
        or request.query_params.get("project_id")
    )
    return pid if pid else None


def _serialize_connection(record, *, plugin_public_view) -> dict[str, Any]:
    """Convert a Connection dataclass to a JSON-serializable dict with masking."""
    return {
        "id": record.id,
        "protocol": record.protocol,
        "project_id": record.project_id,
        "display_name": record.display_name,
        "tags": list(record.tags),
        "credentials_backend": record.credentials_backend,
        "status": record.status,
        "last_tested_at": record.last_tested_at,
        "last_test_ok": record.last_test_ok,
        "is_default": record.is_default,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_error": record.last_error,
        "protocol_data": plugin_public_view(record.protocol_data),
    }


def _decrypted_pd(ctx: ConnectionsContext, record, plugin) -> dict[str, Any]:
    """Decrypt protocol_data for plugin lifecycle hooks / test()."""
    return ctx.router.decrypt(
        record.protocol_data,
        sensitive_field_paths=plugin.sensitive_fields,
        connection_credentials_backend=record.credentials_backend,
    )


# ── Router factory ──────────────────────────────────────────────────


def _build_router(sf: AdminStateFile, ctx: ConnectionsContext) -> APIRouter:
    """Construct the generic connections router bound to an AdminStateFile."""
    from sagewai.admin.autopilot_routes import _require_auth

    router = APIRouter(prefix="/api/v1/admin/connections", tags=["connections"])

    # ── GET /protocols ─────────────────────────────────────────────

    @router.get("/protocols", response_model=None)
    async def list_protocols(request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        return [
            {
                "id": p.id,
                "display_name": p.display_name,
                "sensitive_fields": list(p.sensitive_fields),
            }
            for p in all_protocols()
        ]

    # ── GET /backends ──────────────────────────────────────────────

    @router.get("/backends", response_model=None)
    async def list_backends(request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        return [
            {"id": b.id, "display_name": b.display_name}
            for b in all_backends()
        ]

    # ── GET / ──────────────────────────────────────────────────────

    @router.get("/", response_model=None)
    async def list_connections(
        request: Request,
        protocol: str | None = None,
        tag: str | None = None,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        pid = _project_scope(request)
        records = ctx.store.list(pid, protocol=protocol, tag=tag)
        return [
            _serialize_connection(
                r, plugin_public_view=get_protocol(r.protocol).public_view
            )
            for r in records
        ]

    # ── POST / ─────────────────────────────────────────────────────

    @router.post("/", response_model=None)
    async def create_connection(
        request: Request, payload: CreateConnectionPayload,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        try:
            plugin = get_protocol(payload.protocol)
        except UnknownProtocolError as exc:
            raise HTTPException(400, f"unknown protocol: {exc}")
        # Validate protocol_data via plugin schema
        try:
            plugin.protocol_data_schema()(**payload.protocol_data)
        except ValidationError as exc:
            raise HTTPException(422, detail=exc.errors())
        # Encrypt via router
        encrypted_pd = ctx.router.encrypt(
            payload.protocol_data,
            sensitive_field_paths=plugin.sensitive_fields,
            connection_credentials_backend=payload.credentials_backend,
        )
        pid = _project_scope(request)
        try:
            connection = ctx.store.create(
                protocol=payload.protocol,
                project_id=pid,
                display_name=payload.display_name,
                tags=payload.tags,
                protocol_data=encrypted_pd,
                credentials_backend=payload.credentials_backend,
            )
        except DuplicateDisplayNameError as exc:
            raise HTTPException(409, str(exc))
        # plugin.on_create hook
        plugin_ctx = ctx.make_plugin_context(project_id=pid, request=request)
        try:
            connection = await plugin.on_create(connection, ctx=plugin_ctx)
        except Exception as exc:
            # roll back the store on plugin-hook failure
            ctx.store.delete(connection.id)
            raise HTTPException(500, f"plugin.on_create failed: {exc}")
        return _serialize_connection(connection, plugin_public_view=plugin.public_view)

    # ── GET /{id} ──────────────────────────────────────────────────

    @router.get("/{connection_id}", response_model=None)
    async def get_connection(connection_id: str, request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        record = ctx.store.get(connection_id)
        if record is None:
            raise HTTPException(404, f"connection {connection_id} not found")
        plugin = get_protocol(record.protocol)
        return _serialize_connection(record, plugin_public_view=plugin.public_view)

    # ── PATCH /{id} ────────────────────────────────────────────────

    @router.patch("/{connection_id}", response_model=None)
    async def patch_connection(
        connection_id: str, request: Request, payload: PatchConnectionPayload,
    ) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        before = ctx.store.get(connection_id)
        if before is None:
            raise HTTPException(404, f"connection {connection_id} not found")
        plugin = get_protocol(before.protocol)

        update_fields: dict[str, Any] = {}
        if payload.display_name is not None:
            update_fields["display_name"] = payload.display_name
        if payload.tags is not None:
            update_fields["tags"] = payload.tags

        new_backend = payload.credentials_backend
        backend_changed = (
            "credentials_backend" in payload.model_fields_set
            and new_backend != before.credentials_backend
        )

        if payload.protocol_data is not None:
            # Re-validate via plugin schema
            try:
                plugin.protocol_data_schema()(**payload.protocol_data)
            except ValidationError as exc:
                raise HTTPException(422, detail=exc.errors())
            target_backend = new_backend if backend_changed else before.credentials_backend
            encrypted_pd = ctx.router.encrypt(
                payload.protocol_data,
                sensitive_field_paths=plugin.sensitive_fields,
                connection_credentials_backend=target_backend,
            )
            update_fields["protocol_data"] = encrypted_pd
        elif backend_changed:
            # No protocol_data change but backend changed → swap re-encryption.
            swapped = ctx.router.swap(
                before.protocol_data,
                sensitive_field_paths=plugin.sensitive_fields,
                old_credentials_backend=before.credentials_backend,
                new_credentials_backend=new_backend,
            )
            update_fields["protocol_data"] = swapped

        if backend_changed:
            update_fields["credentials_backend"] = new_backend

        if not update_fields:
            return _serialize_connection(before, plugin_public_view=plugin.public_view)

        try:
            after = ctx.store.update(connection_id, **update_fields)
        except DuplicateDisplayNameError as exc:
            raise HTTPException(409, str(exc))

        plugin_ctx = ctx.make_plugin_context(
            project_id=_project_scope(request), request=request,
        )
        try:
            after = await plugin.on_update(before, after, ctx=plugin_ctx)
        except Exception as exc:
            raise HTTPException(500, f"plugin.on_update failed: {exc}")
        return _serialize_connection(after, plugin_public_view=plugin.public_view)

    # ── DELETE /{id} ───────────────────────────────────────────────

    @router.delete("/{connection_id}", status_code=204, response_model=None)
    async def delete_connection(connection_id: str, request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        record = ctx.store.get(connection_id)
        if record is None:
            raise HTTPException(404, f"connection {connection_id} not found")
        plugin = get_protocol(record.protocol)
        plugin_ctx = ctx.make_plugin_context(
            project_id=_project_scope(request), request=request,
        )
        try:
            await plugin.on_delete(record, ctx=plugin_ctx)
        except Exception as exc:
            raise HTTPException(500, f"plugin.on_delete failed: {exc}")
        ok = ctx.store.delete(connection_id)
        if not ok:
            raise HTTPException(404, f"connection {connection_id} not found")
        return Response(status_code=204)

    # ── POST /{id}/test ────────────────────────────────────────────

    @router.post("/{connection_id}/test", response_model=None)
    async def test_connection(connection_id: str, request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        record = ctx.store.get(connection_id)
        if record is None:
            raise HTTPException(404, f"connection {connection_id} not found")
        plugin = get_protocol(record.protocol)
        # Pass the connection through with DECRYPTED protocol_data so the
        # plugin's test() method sees real values.
        try:
            decrypted_pd = _decrypted_pd(ctx, record, plugin)
        except Exception as exc:
            raise HTTPException(500, f"decrypt failed: {exc}")
        # Build a synthetic Connection record with decrypted data
        from dataclasses import replace
        decrypted_record = replace(record, protocol_data=decrypted_pd)
        plugin_ctx = ctx.make_plugin_context(
            project_id=_project_scope(request), request=request,
        )
        result = await plugin.test(decrypted_record, ctx=plugin_ctx)
        ctx.store.update_test_result(connection_id, ok=result.ok)
        return {
            "ok": result.ok,
            "status_code": result.status_code,
            "message": result.message,
        }

    # ── POST /{id}/set-default ─────────────────────────────────────

    @router.post("/{connection_id}/set-default", response_model=None)
    async def set_default(connection_id: str, request: Request) -> Any:
        err = _require_auth(request, sf)
        if err is not None:
            return err
        try:
            record = ctx.store.set_default(connection_id)
        except ConnectionNotFoundError:
            raise HTTPException(404, f"connection {connection_id} not found")
        plugin = get_protocol(record.protocol)
        return _serialize_connection(record, plugin_public_view=plugin.public_view)

    return router


# ── Wiring ──────────────────────────────────────────────────────────


def register(app: FastAPI, sf: AdminStateFile) -> None:
    """Mount the generic connections router + plugin extra_routes."""
    ctx = build_connections_context(sf)
    # Inject the context for plugin extra_routes that need it (oauth2).
    from sagewai.connections.protocols import oauth2 as oauth2_module
    oauth2_module._test_inject_context(ctx)

    app.include_router(_build_router(sf, ctx))
    # Mount each plugin's extra_routes at /api/v1/admin/connections/<plugin.id>/.
    for plugin in all_protocols():
        sub_router = plugin.extra_routes()
        if sub_router is None:
            continue
        # Skip empty routers (no routes registered).
        if not getattr(sub_router, "routes", []):
            continue
        app.include_router(
            sub_router,
            prefix=f"/api/v1/admin/connections/{plugin.id}",
            tags=[f"connections-{plugin.id}"],
        )


__all__ = ["register"]
