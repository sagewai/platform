# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Smoke test that the public sagewai_llm API is importable and complete."""

from __future__ import annotations


def test_public_surface_is_importable():
    from sagewai.autopilot.sagewai_llm import (  # noqa: F401
        QUOTA_HEADER,
        BlueprintCache,
        ClientError,
        ClientUnreachable,
        FeedResponse,
        FileIdentityStore,
        GenerateBlueprintRequest,
        GenerateBlueprintResponse,
        InstanceIdentity,
        InstanceIdentityStore,
        PublishBlueprintRequest,
        PublishBlueprintResponse,
        QuotaExceeded,
        QuotaResponse,
        QuotaStatus,
        RetrieveBlueprintsRequest,
        RetrieveBlueprintsResponse,
        RunEvalRequest,
        RunEvalResponse,
        SagewaiLLMClient,
        ServiceError,
        SignatureError,
        TelemetryEvent,
        build_signed_headers,
        ensure_identity,
        parse_quota_header,
        sign_request,
        verify_signature,
    )


def test_public_all_lists_every_export():
    import sagewai.autopilot.sagewai_llm as sllm

    for name in sllm.__all__:
        assert hasattr(sllm, name), f"{name} listed in __all__ but not exported"
