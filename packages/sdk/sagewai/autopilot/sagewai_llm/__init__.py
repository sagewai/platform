# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai LLM — async HTTP client for the proprietary hosted blueprint service.

Public API surface for the client. This subpackage is the open-source
client for the closed-source hosted service. It owns identity, request
signing, the local cache, and graceful degradation. It does NOT contain
any production blueprints — those live only on the server.
"""

from __future__ import annotations

from .cache import BlueprintCache
from .client import DEFAULT_BASE_URL, DEFAULT_TIMEOUT_SECONDS, SagewaiLLMClient
from .errors import (
    ClientError,
    ClientUnreachable,
    QuotaExceeded,
    ServiceError,
    SignatureError,
)
from .identity import (
    FileIdentityStore,
    InstanceIdentity,
    InstanceIdentityStore,
    ensure_identity,
)
from .quota import QUOTA_HEADER, QuotaStatus, parse_quota_header
from .signing import (
    INSTANCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    build_signed_headers,
    sign_request,
    verify_signature,
)
from .types import (
    FeedResponse,
    GenerateBlueprintRequest,
    GenerateBlueprintResponse,
    PublishBlueprintRequest,
    PublishBlueprintResponse,
    QuotaResponse,
    RetrieveBlueprintsRequest,
    RetrieveBlueprintsResponse,
    RetrieveCandidate,
    RunEvalRequest,
    RunEvalResponse,
    TelemetryEvent,
)

__all__ = [
    # Client
    "SagewaiLLMClient",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    # Errors
    "ClientError",
    "ClientUnreachable",
    "QuotaExceeded",
    "ServiceError",
    "SignatureError",
    # Identity
    "InstanceIdentity",
    "InstanceIdentityStore",
    "FileIdentityStore",
    "ensure_identity",
    # Signing
    "sign_request",
    "verify_signature",
    "build_signed_headers",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "INSTANCE_HEADER",
    # Quota
    "QuotaStatus",
    "parse_quota_header",
    "QUOTA_HEADER",
    # Cache
    "BlueprintCache",
    # Types
    "GenerateBlueprintRequest",
    "GenerateBlueprintResponse",
    "RetrieveBlueprintsRequest",
    "RetrieveBlueprintsResponse",
    "RetrieveCandidate",
    "PublishBlueprintRequest",
    "PublishBlueprintResponse",
    "FeedResponse",
    "TelemetryEvent",
    "RunEvalRequest",
    "RunEvalResponse",
    "QuotaResponse",
]
