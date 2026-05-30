# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Protocol plugin registry.

PR2 ships 5 plugins. PR3 doesn't add or remove any; PR4 wires them into
the admin + CLI surfaces. The :data:`PROTOCOLS` tuple is the registry —
:func:`get_protocol` and :func:`all_protocols` are the public lookup API.

NOTE: this file is filled in incrementally across Task 1–9. After Task 1
it only re-exports contract types; each subsequent plugin task appends
the plugin instance to ``PROTOCOLS`` and re-exports it.
"""
from sagewai.connections.errors import UnknownProtocolError
from sagewai.connections.protocols.base import (
    PluginContext,
    ProtocolPlugin,
    TestResult,
)
from sagewai.connections.protocols.coap import CoapProtocolPlugin
from sagewai.connections.protocols.http import HttpProtocolPlugin
from sagewai.connections.protocols.inference import (
    InferenceProtocolPlugin,
    inference_default_key,
)
from sagewai.connections.protocols.mcp import McpProtocolPlugin
from sagewai.connections.protocols.modbus import ModbusProtocolPlugin
from sagewai.connections.protocols.mqtt import MqttProtocolPlugin
from sagewai.connections.protocols.oauth2 import (
    OAuth2ProtocolPlugin,
    oauth2_default_key,
)
from sagewai.connections.protocols.opcua import OpcuaProtocolPlugin
from sagewai.connections.protocols.sdk import SdkProtocolPlugin
from sagewai.connections.protocols.websocket import WebsocketProtocolPlugin


PROTOCOLS: tuple[ProtocolPlugin, ...] = (
    HttpProtocolPlugin(),
    SdkProtocolPlugin(),
    McpProtocolPlugin(),
    InferenceProtocolPlugin(),
    OAuth2ProtocolPlugin(),
    CoapProtocolPlugin(),
    ModbusProtocolPlugin(),
    OpcuaProtocolPlugin(),
    WebsocketProtocolPlugin(),  # ← Phase A complete
    MqttProtocolPlugin(),  # ← Phase B — first subscription-capable protocol
)
_BY_ID: dict[str, ProtocolPlugin] = {p.id: p for p in PROTOCOLS}

# Default-key extractors keyed by protocol id. PR4 passes this dict to
# the generic ConnectionStore so set_default / first-default semantics
# work per-provider for oauth2 + inference plugins.
DEFAULT_KEY_FOR: dict[str, "callable"] = {
    "inference": inference_default_key,
    "oauth2": oauth2_default_key,
}


def get_protocol(protocol_id: str) -> ProtocolPlugin:
    """Look up a plugin by id; raises ``UnknownProtocolError`` if absent."""
    try:
        return _BY_ID[protocol_id]
    except KeyError as e:
        raise UnknownProtocolError(protocol_id) from e


def all_protocols() -> tuple[ProtocolPlugin, ...]:
    """Return every registered plugin in declaration order."""
    return PROTOCOLS


__all__ = [
    "CoapProtocolPlugin",
    "DEFAULT_KEY_FOR",
    "HttpProtocolPlugin",
    "InferenceProtocolPlugin",
    "McpProtocolPlugin",
    "ModbusProtocolPlugin",
    "MqttProtocolPlugin",
    "OAuth2ProtocolPlugin",
    "OpcuaProtocolPlugin",
    "PROTOCOLS",
    "PluginContext",
    "ProtocolPlugin",
    "SdkProtocolPlugin",
    "TestResult",
    "UnknownProtocolError",
    "WebsocketProtocolPlugin",
    "all_protocols",
    "get_protocol",
    "inference_default_key",
    "oauth2_default_key",
]
