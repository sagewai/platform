# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Inference provider plugin.

Wraps the existing inference-provider logic from
:mod:`sagewai.admin.connections_routes` (PROVIDER_KEYS, PROVIDER_SCHEMA)
and :mod:`sagewai.admin.provider_probes` (per-provider test probes).

PR4 deletes the old admin route handlers once the new generic routes
mount this plugin. PR2 keeps the old routes serving traffic; the plugin
exists in isolation for testing.
"""
from __future__ import annotations

from typing import Any, ClassVar

import click
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sagewai.admin.connections_routes import PROVIDER_KEYS
from sagewai.connections.models import Connection, TestResult
from sagewai.connections.protocols.base import PluginContext


def inference_default_key(protocol_data: dict[str, Any]) -> str | None:
    """Default-key extractor used by the generic store.

    Each ``(project_id, "inference", provider_key)`` group has its own
    default flag — multiple Modal endpoints carry default independently
    of RunPod endpoints.
    """
    pd = protocol_data
    return pd.get("provider_key") if isinstance(pd, dict) else None


class InferenceProtocolData(BaseModel):
    """Validation schema for inference connections."""

    model_config = ConfigDict(extra="allow")

    provider_key: str = Field(..., min_length=1)
    base_url: str | None = None
    model_name: str | None = None
    secrets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _provider_key_in_allowed(self):
        if self.provider_key not in PROVIDER_KEYS:
            raise ValueError(
                f"provider_key {self.provider_key!r} not in {tuple(PROVIDER_KEYS)!r}"
            )
        # custom requires base_url
        if self.provider_key == "custom" and not self.base_url:
            raise ValueError("custom provider_key requires base_url")
        return self


async def _run_probe(
    provider_key: str, secrets: dict, base_url: str | None
) -> dict:
    """Dispatch to the right probe in :mod:`sagewai.admin.provider_probes`.

    Returns ``{"ok": bool, "detail": str}``.

    Lookup order:
      1. ``probe_<provider_key>`` — future-safe per-provider probe
         (matches the naming convention PR4 may introduce).
      2. ``test_cloud_provider(provider_name, config)`` — the existing
         generic cloud-LLM probe (returns ``{connected, latency_ms,
         models?, error?}``). We normalize the keys.
    """
    from sagewai.admin import provider_probes

    fn = getattr(provider_probes, f"probe_{provider_key}", None)
    if fn is not None:
        raw = await fn(secrets=secrets, base_url=base_url)
    else:
        # Fall back to the generic test_cloud_provider surface. Coerce
        # the secrets dict into a {base_url, api_key} config.
        api_key = ""
        if isinstance(secrets, dict):
            # First non-empty value: most providers store one key.
            for v in secrets.values():
                if v:
                    api_key = v
                    break
        config = {"base_url": base_url or "", "api_key": api_key}
        try:
            raw = await provider_probes.test_cloud_provider(provider_key, config)
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    if isinstance(raw, dict):
        # Normalize shapes:
        # - test_cloud_provider returns {connected, latency_ms, error?}
        # - future probe_<provider> may return {ok, detail}
        if "ok" in raw:
            return {"ok": bool(raw["ok"]), "detail": raw.get("detail") or raw.get("error") or ""}
        if "connected" in raw:
            return {
                "ok": bool(raw["connected"]),
                "detail": raw.get("error") or raw.get("note") or "",
            }
        return {"ok": False, "detail": "unknown probe result shape"}
    if hasattr(raw, "model_dump"):
        d = raw.model_dump()
        return {
            "ok": bool(d.get("ok") or d.get("connected")),
            "detail": d.get("detail") or d.get("error") or "",
        }
    return {"ok": bool(raw), "detail": str(raw)}


@click.command("test")
@click.argument("connection_id")
def _test_cmd(connection_id: str) -> None:
    """Test an inference connection — placeholder until PR4 wires the CLI."""
    click.echo(f"testing inference connection {connection_id} (full wiring lands in PR4)")


class InferenceProtocolPlugin:
    id: ClassVar[str] = "inference"
    display_name: ClassVar[str] = "Inference provider"
    sensitive_fields: ClassVar[tuple[str, ...]] = ()  # per-record derived; see public_view

    def protocol_data_schema(self) -> type[BaseModel]:
        return InferenceProtocolData

    def public_view(
        self, protocol_data: dict[str, Any], *, include_secrets: bool = False
    ) -> dict[str, Any]:
        out = dict(protocol_data)
        if include_secrets:
            return out
        # Mask every value in `secrets`. For inference, all `secrets.*` values
        # are sensitive by definition.
        secrets = out.get("secrets")
        if isinstance(secrets, dict):
            out["secrets"] = {k: ("***" if v else "") for k, v in secrets.items()}
        return out

    async def on_create(self, connection: Connection, *, ctx: PluginContext) -> Connection:
        return connection

    async def on_update(
        self, before: Connection, after: Connection, *, ctx: PluginContext
    ) -> Connection:
        return after

    async def on_delete(self, connection: Connection, *, ctx: PluginContext) -> None:
        return None

    async def test(self, connection: Connection, *, ctx: PluginContext) -> TestResult:
        data = connection.protocol_data
        result = await _run_probe(
            provider_key=data.get("provider_key"),
            secrets=data.get("secrets", {}),
            base_url=data.get("base_url"),
        )
        return TestResult(
            ok=bool(result.get("ok")),
            message=result.get("detail") or None,
        )

    def extra_routes(self) -> APIRouter:
        return APIRouter()

    def extra_cli(self) -> list[click.Command]:
        return [_test_cmd]


__all__ = [
    "InferenceProtocolData",
    "InferenceProtocolPlugin",
    "inference_default_key",
]
