# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Provider CLI commands — manage configured LLM providers and the default."""

from __future__ import annotations

import click

import sagewai.cli as _cli


@click.group()
def provider() -> None:
    """Manage configured LLM providers and the default for the autopilot.

    \b
    Examples:
      sagewai provider list
      sagewai provider add openai --api-key sk-... --default
      sagewai provider set-default openai
      sagewai provider remove prov-openai
    """


@provider.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def provider_list(as_json: bool) -> None:
    """List configured LLM providers."""
    data = _cli._api_get("/api/v1/providers") or []
    if as_json:
        _cli._echo_json(data)
        return
    if not data:
        click.echo("No providers configured. Run `sagewai provider add <name> --api-key ...` to add one.")
        return
    rows = [
        {
            "id": p.get("id", ""),
            "name": p.get("provider_name", ""),
            "default": "yes" if p.get("default") else "",
            "status": p.get("status", ""),
            "env_var_set": "yes" if p.get("env_var_set") else "no",
        }
        for p in data
    ]
    _cli._echo_table(rows, ["id", "name", "default", "status", "env_var_set"])


_SELF_HOSTED = {"ollama", "lmstudio", "vllm"}


@provider.command("add")
@click.argument("provider_name")
@click.option("--api-key", "api_key", default=None, help="API key (cloud providers).")
@click.option(
    "--base-url",
    "base_url",
    default=None,
    help="Base URL for self-hosted providers (e.g. http://localhost:11434 for ollama).",
)
@click.option(
    "--model",
    "model",
    default=None,
    help="Default model name (e.g. 'qwen2.5:7b' for ollama). Auto-prefixed with the provider for litellm.",
)
@click.option("--display-name", "display_name", default=None, help="Human-readable label.")
@click.option(
    "--provider-type",
    "provider_type",
    default=None,
    help="Provider type (cloud, self-hosted). Auto-detected from name if omitted.",
)
@click.option(
    "--default",
    "make_default",
    is_flag=True,
    help="Mark this provider as the default for the autopilot.",
)
def provider_add(
    provider_name: str,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    display_name: str | None,
    provider_type: str | None,
    make_default: bool,
) -> None:
    """Add or update a provider config.

    \b
    Examples:
      # Cloud provider
      sagewai provider add openai --api-key sk-... --default

      # Local Ollama (no API key needed)
      sagewai provider add ollama --model qwen2.5:7b --default
      sagewai provider add ollama --base-url http://localhost:11434 --model llama3.2:1b
    """
    if provider_type is None:
        provider_type = "self-hosted" if provider_name in _SELF_HOSTED else "cloud"

    config: dict[str, object] = {}
    if api_key:
        config["api_key"] = api_key
    if base_url:
        config["base_url"] = base_url
    if model:
        config["model"] = model

    body: dict[str, object] = {
        "provider_name": provider_name,
        "provider_type": provider_type,
        "display_name": display_name or provider_name,
        "config": config,
        "default": make_default,
    }
    data = _cli._api_post("/api/v1/providers", body)
    pid = data.get("id") if isinstance(data, dict) else None
    click.echo(f"Provider configured: {provider_name} (id={pid or '?'}{', default' if make_default else ''})")


@provider.command("set-default")
@click.argument("provider")
def provider_set_default(provider: str) -> None:
    """Set *provider* (id or name) as the default LLM."""
    data = _cli._api_post(f"/api/v1/providers/{provider}/default", {})
    if isinstance(data, dict) and data.get("status") == "ok":
        click.echo(f"Default provider set: {data.get('id') or provider}")
        return
    click.echo(f"Failed to set default: {data}", err=True)


@provider.command("remove")
@click.argument("provider_id")
def provider_remove(provider_id: str) -> None:
    """Remove a provider config by id."""
    _cli._api_delete(f"/api/v1/providers/{provider_id}")
    click.echo(f"Provider removed: {provider_id}")
