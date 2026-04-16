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
