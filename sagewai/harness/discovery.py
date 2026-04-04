# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Local LLM auto-discovery for the harness.

Probes well-known local inference server ports to discover available
models, enabling zero-config routing to locally-served LLMs.

Supported servers:
- **Ollama** — port 11434, ``/api/tags``
- **LM Studio** — port 1234, ``/v1/models``
- **Unsloth** (llama-server) — port 8001, ``/v1/models``
- **vLLM** — port 8000, ``/v1/models``
- **LocalAI** — port 8080, ``/v1/models``

Usage::

    from sagewai.harness.discovery import discover_local_backends

    backends = await discover_local_backends()
    for name, info in backends.items():
        print(f"{name}: {info['models']} at {info['base_url']}")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Well-known local inference servers and their default ports / probe paths.
_LOCAL_SERVERS: list[dict[str, Any]] = [
    {
        "name": "ollama",
        "base_url": "http://localhost:11434",
        "probe_path": "/api/tags",
        "models_path": "/api/tags",
        "models_key": "models",
        "model_name_key": "name",
        "openai_compat_url": "http://localhost:11434",
    },
    {
        "name": "lm-studio",
        "base_url": "http://localhost:1234",
        "probe_path": "/v1/models",
        "models_path": "/v1/models",
        "models_key": "data",
        "model_name_key": "id",
        "openai_compat_url": "http://localhost:1234",
    },
    {
        "name": "unsloth",
        "base_url": "http://localhost:8001",
        "probe_path": "/v1/models",
        "models_path": "/v1/models",
        "models_key": "data",
        "model_name_key": "id",
        "openai_compat_url": "http://localhost:8001",
    },
    {
        "name": "vllm",
        "base_url": "http://localhost:8000",
        "probe_path": "/v1/models",
        "models_path": "/v1/models",
        "models_key": "data",
        "model_name_key": "id",
        "openai_compat_url": "http://localhost:8000",
    },
    {
        "name": "localai",
        "base_url": "http://localhost:8080",
        "probe_path": "/v1/models",
        "models_path": "/v1/models",
        "models_key": "data",
        "model_name_key": "id",
        "openai_compat_url": "http://localhost:8080",
    },
]


@dataclass
class DiscoveredServer:
    """A discovered local LLM inference server."""

    name: str
    base_url: str
    openai_compat_url: str
    models: list[str] = field(default_factory=list)
    healthy: bool = True


async def probe_server(
    server_config: dict[str, Any],
    *,
    timeout: float = 3.0,
) -> DiscoveredServer | None:
    """Probe a single local server for availability and models.

    Args:
        server_config: Server configuration from ``_LOCAL_SERVERS``.
        timeout: HTTP timeout in seconds (short — local only).

    Returns:
        A ``DiscoveredServer`` if reachable, or ``None``.
    """
    name = server_config["name"]
    base_url = server_config["base_url"]
    probe_path = server_config["probe_path"]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url}{probe_path}")
            if resp.status_code != 200:
                return None

            data = resp.json()
            models_key = server_config["models_key"]
            name_key = server_config["model_name_key"]

            raw_models = data.get(models_key, [])
            model_names = []
            for m in raw_models:
                if isinstance(m, dict):
                    model_names.append(m.get(name_key, "unknown"))
                elif isinstance(m, str):
                    model_names.append(m)

            logger.info(
                "Discovered %s at %s with %d model(s): %s",
                name,
                base_url,
                len(model_names),
                ", ".join(model_names[:5]),
            )

            return DiscoveredServer(
                name=name,
                base_url=base_url,
                openai_compat_url=server_config["openai_compat_url"],
                models=model_names,
            )

    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        # Server not running — expected for most probes
        return None
    except (ValueError, KeyError) as exc:
        logger.debug("Failed to parse %s response: %s", name, exc)
        return None


async def discover_local_backends(
    *,
    timeout: float = 3.0,
    additional_servers: list[dict[str, Any]] | None = None,
) -> dict[str, DiscoveredServer]:
    """Probe all known local LLM servers and return discovered ones.

    Args:
        timeout: HTTP timeout per probe in seconds.
        additional_servers: Extra server configs to probe (same format
            as ``_LOCAL_SERVERS`` entries).

    Returns:
        Dict mapping server name to ``DiscoveredServer``.
    """
    servers = list(_LOCAL_SERVERS)
    if additional_servers:
        servers.extend(additional_servers)

    discovered: dict[str, DiscoveredServer] = {}

    # Fan out all probes concurrently (each has its own timeout).
    results = await asyncio.gather(
        *(probe_server(c, timeout=timeout) for c in servers),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, DiscoveredServer) and result.models:
            discovered[result.name] = result

    if discovered:
        total_models = sum(len(s.models) for s in discovered.values())
        logger.info(
            "Local LLM discovery: %d server(s), %d model(s) total",
            len(discovered),
            total_models,
        )
    else:
        logger.debug("Local LLM discovery: no servers found")

    return discovered


def build_local_backends(
    discovered: dict[str, DiscoveredServer],
) -> dict[str, Any]:
    """Build harness-compatible backend instances from discovered servers.

    Returns a dict of ``{server_name: OpenAIBackend}`` instances
    ready for use in the harness proxy.

    Args:
        discovered: Output from ``discover_local_backends()``.

    Returns:
        Dict mapping server name to ``OpenAIBackend`` instances.
    """
    from sagewai.harness.backend import OpenAIBackend

    backends: dict[str, Any] = {}
    for name, server in discovered.items():
        backends[name] = OpenAIBackend(
            api_key="",  # Local servers don't need auth
            base_url=server.openai_compat_url,
        )
    return backends


# Cost tracking: local models are free.
LOCAL_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # All local models cost $0 per token
    "_local_default": (0.0, 0.0),
}
