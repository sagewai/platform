# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Admin routes for /api/v1/admin/inference-providers/* (Gap #10).

Credential vault for the four cloud GPU providers + custom-endpoint
config. Sealed-encrypted at rest via the existing Sealed Crypto/master
key. Project-scoped via ``X-Project-ID``.

Companion examples:
- 44_colab_drive_orchestration  (Tier 1 — free CUDA via Colab)
- 45_vastai_marketplace         (Tier 2 — Vast.ai bid-cheapest)
- 46_custom_inference_endpoint  (Tier 5 — bring-your-own endpoint)
- 47_runpod_finetune            (Tier 3 — RunPod gold standard)
- 48_modal_serverless           (Tier 4 — Modal serverless inference)
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from sagewai.admin.state_file import AdminStateFile
from sagewai.sealed.crypto import Crypto, SecretCorrupted
from sagewai.sealed.master_key import MasterKeyMissing, resolve_master_key

# ── Provider catalog ────────────────────────────────────────────────

# Provider keys mirror the env-var names used by the inference-spectrum
# examples (Examples 44–48). The list deliberately omits Spheron (the
# v1.0 landscape doc preferred Vast.ai for its seven-year track record
# and per-host reliability scoring; see
# atelier/docs/v1.0/inference-provisioning-landscape.md).
PROVIDER_KEYS = ("runpod", "modal", "vastai", "colab", "custom")

# Required secret keys per provider — used to validate write payloads
# and to render the per-provider modal in the admin UI.
PROVIDER_SCHEMA: dict[str, dict[str, Any]] = {
    "runpod": {
        "label": "RunPod",
        "tagline": "The gold standard for CLI automation.",
        "secret_keys": ["RUNPOD_API_KEY"],
        "env_keys": [],
        "example": "47_runpod_finetune_orchestration",
    },
    "modal": {
        "label": "Modal",
        "tagline": "Serverless inference, per-second billing.",
        "secret_keys": ["MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"],
        "env_keys": [],
        "example": "48_modal_serverless_inference",
    },
    "vastai": {
        "label": "Vast.ai",
        "tagline": "Budget-tier marketplace with reliability scoring.",
        "secret_keys": ["VASTAI_API_KEY"],
        "env_keys": [],
        "example": "45_vastai_marketplace_bid",
    },
    "colab": {
        "label": "Google Colab",
        "tagline": "Free Tesla T4 via Drive-sync orchestration.",
        # Colab uses Google OAuth; we accept the JSON client config
        # blob as a single secret (the operator pastes the file
        # contents).
        "secret_keys": ["GOOGLE_DRIVE_OAUTH_JSON"],
        "env_keys": [],
        # Companion example #44 lands separately (atelier issue #44 / Gap #8d).
        "example": None,
    },
    "custom": {
        "label": "Custom endpoint",
        "tagline": "Bring-your-own OpenAI-compatible HTTP endpoint.",
        # Auth value is held under a single key — its meaning is
        # determined by ``auth_shape`` on the metadata.
        "secret_keys": ["CUSTOM_AUTH_VALUE"],
        "env_keys": ["CUSTOM_BASE_URL", "CUSTOM_MODEL_NAME"],
        "example": "46_custom_inference_as_tool",
    },
}

_AUTH_SHAPES = ("none", "bearer", "basic", "sigv4")


# ── Storage (Sealed-encrypted JSON sub-store) ───────────────────────

_DEFAULT_STORE_PATH = Path.home() / ".sagewai" / "inference-providers.json"


def _store_path() -> Path:
    """Resolve the on-disk path. Honours ``SAGEWAI_ADMIN_STATE_FILE``
    so test harnesses can sandbox the credential vault alongside the
    main admin-state file."""
    state_env = os.environ.get("SAGEWAI_ADMIN_STATE_FILE")
    if state_env:
        return Path(state_env).parent / "inference-providers.json"
    return _DEFAULT_STORE_PATH


_LOCK = asyncio.Lock()


def _read_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {"version": 1, "providers": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "providers": []}


def _write_store(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=path.parent, prefix=".inference-providers.", suffix=".tmp",
    ) as tmp:
        json.dump(store, tmp, indent=2, default=str)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)


def _crypto() -> Crypto:
    """Resolve the Sealed master key. Raises if not configured."""
    key, _ = resolve_master_key()
    return Crypto(key)


# ── Pydantic models ─────────────────────────────────────────────────


class InferenceProviderMetadata(BaseModel):
    """Provider entry without decrypted secret values."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    label: str
    tagline: str
    project_id: str | None = None
    configured: bool
    last_updated_at: str | None = None
    last_tested_at: str | None = None
    last_test_ok: bool | None = None
    last_test_detail: str | None = None
    secret_keys: list[str] = Field(default_factory=list)
    # Non-secret metadata (e.g. base_url, model_name, auth_shape for custom)
    env: dict[str, str] = Field(default_factory=dict)
    example_pointer: str | None = None
    docs_pointer: str = "/docs/inference"


class InferenceProviderWritePayload(BaseModel):
    """Request body for upserting credentials."""

    model_config = ConfigDict(extra="forbid")

    secrets: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    auth_shape: str | None = None  # custom only: none/bearer/basic/sigv4


class InferenceProviderTestResult(BaseModel):
    ok: bool
    detail: str
    provider: str
    tested_at: str


# ── Helpers ─────────────────────────────────────────────────────────


def _validate_provider(provider: str) -> dict[str, Any]:
    if provider not in PROVIDER_SCHEMA:
        raise HTTPException(
            status_code=404,
            detail={
                "provider": provider,
                "supported": list(PROVIDER_SCHEMA.keys()),
            },
        )
    return PROVIDER_SCHEMA[provider]


def _project_scope(request: Request) -> str | None:
    pid = request.headers.get("x-project-id") or request.query_params.get(
        "project_id"
    )
    return pid if pid else None


def _row_to_metadata(row: dict[str, Any]) -> InferenceProviderMetadata:
    schema = PROVIDER_SCHEMA[row["provider"]]
    return InferenceProviderMetadata(
        provider=row["provider"],
        label=schema["label"],
        tagline=schema["tagline"],
        project_id=row.get("project_id"),
        configured=bool(row.get("secrets")),
        last_updated_at=row.get("last_updated_at"),
        last_tested_at=row.get("last_tested_at"),
        last_test_ok=row.get("last_test_ok"),
        last_test_detail=row.get("last_test_detail"),
        secret_keys=sorted((row.get("secrets") or {}).keys()),
        env=row.get("env") or {},
        example_pointer=schema.get("example"),
    )


def _empty_metadata(provider: str, project_id: str | None) -> InferenceProviderMetadata:
    schema = PROVIDER_SCHEMA[provider]
    return InferenceProviderMetadata(
        provider=provider,
        label=schema["label"],
        tagline=schema["tagline"],
        project_id=project_id,
        configured=False,
        secret_keys=[],
        env={},
        example_pointer=schema.get("example"),
    )


def _find_row(
    store: dict[str, Any], provider: str, project_id: str | None,
) -> tuple[int | None, dict[str, Any] | None]:
    rows = store.get("providers") or []
    for i, row in enumerate(rows):
        if row.get("provider") == provider and row.get("project_id") == project_id:
            return i, row
    return None, None


# ── Router ──────────────────────────────────────────────────────────


router = APIRouter(prefix="/api/v1/admin/inference-providers", tags=["inference-providers"])


@router.get("/catalog")
async def get_catalog() -> dict[str, Any]:
    """Static provider catalog — labels, schema, example pointers.

    The frontend uses this to render per-provider modal forms without
    hardcoding the schema in TypeScript.
    """
    return {
        "providers": [
            {
                "provider": key,
                "label": schema["label"],
                "tagline": schema["tagline"],
                "secret_keys": schema["secret_keys"],
                "env_keys": schema["env_keys"],
                "example": schema.get("example"),
            }
            for key, schema in PROVIDER_SCHEMA.items()
        ],
        "auth_shapes": list(_AUTH_SHAPES),
    }


@router.get("", response_model=list[InferenceProviderMetadata])
async def list_providers(request: Request) -> list[InferenceProviderMetadata]:
    """Return all five provider cards for the active project scope.

    Always returns one entry per provider — unconfigured providers come
    back with ``configured=False`` so the frontend can render a card
    grid without separately fetching the catalog.
    """
    pid = _project_scope(request)
    async with _LOCK:
        store = _read_store()
    by_provider: dict[str, dict[str, Any]] = {}
    for row in store.get("providers") or []:
        if row.get("project_id") == pid:
            by_provider[row["provider"]] = row
    out: list[InferenceProviderMetadata] = []
    for key in PROVIDER_KEYS:
        row = by_provider.get(key)
        if row is None:
            out.append(_empty_metadata(key, pid))
        else:
            out.append(_row_to_metadata(row))
    return out


@router.get("/{provider}", response_model=InferenceProviderMetadata)
async def get_provider(
    provider: str, request: Request,
) -> InferenceProviderMetadata:
    _validate_provider(provider)
    pid = _project_scope(request)
    async with _LOCK:
        store = _read_store()
    _, row = _find_row(store, provider, pid)
    if row is None:
        return _empty_metadata(provider, pid)
    return _row_to_metadata(row)


@router.put("/{provider}", response_model=InferenceProviderMetadata)
async def upsert_provider(
    provider: str,
    payload: InferenceProviderWritePayload,
    request: Request,
) -> InferenceProviderMetadata:
    schema = _validate_provider(provider)
    pid = _project_scope(request)

    # Validate provided secret keys against the schema. Unknown keys are
    # rejected; missing keys are allowed (operator may save partial
    # config and complete later, e.g. Modal needs both ID + secret).
    expected = set(schema["secret_keys"])
    provided = set(payload.secrets.keys())
    unknown = provided - expected
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={
                "provider": provider,
                "unknown_secret_keys": sorted(unknown),
                "expected": sorted(expected),
            },
        )
    if provider == "custom" and payload.auth_shape:
        if payload.auth_shape not in _AUTH_SHAPES:
            raise HTTPException(
                status_code=400,
                detail={"auth_shape": payload.auth_shape, "expected": list(_AUTH_SHAPES)},
            )

    try:
        crypto = _crypto()
    except MasterKeyMissing as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Sealed master key is not configured. Run "
                "`sagewai admin sealed init` (or set SAGEWAI_MASTER_KEY) "
                "before saving provider credentials. " + str(exc)
            ),
        ) from None

    encrypted = {k: crypto.encrypt(v) for k, v in payload.secrets.items()}
    env = dict(payload.env or {})
    if provider == "custom" and payload.auth_shape:
        env["CUSTOM_AUTH_SHAPE"] = payload.auth_shape

    now = datetime.now(timezone.utc).isoformat()
    async with _LOCK:
        store = _read_store()
        idx, existing = _find_row(store, provider, pid)
        merged_secrets = dict((existing or {}).get("secrets") or {})
        merged_secrets.update(encrypted)
        merged_env = dict((existing or {}).get("env") or {})
        merged_env.update(env)
        row = {
            "provider": provider,
            "project_id": pid,
            "secrets": merged_secrets,
            "env": merged_env,
            "last_updated_at": now,
            # Reset the stale test result on every credential change.
            "last_tested_at": None,
            "last_test_ok": None,
            "last_test_detail": None,
        }
        if idx is None:
            store.setdefault("providers", []).append(row)
        else:
            store["providers"][idx] = row
        _write_store(store)
    return _row_to_metadata(row)


@router.delete("/{provider}", status_code=204)
async def delete_provider(provider: str, request: Request) -> None:
    _validate_provider(provider)
    pid = _project_scope(request)
    async with _LOCK:
        store = _read_store()
        before = len(store.get("providers") or [])
        store["providers"] = [
            r for r in (store.get("providers") or [])
            if not (r.get("provider") == provider and r.get("project_id") == pid)
        ]
        if len(store["providers"]) == before:
            raise HTTPException(
                status_code=404,
                detail={"provider": provider, "project_id": pid},
            )
        _write_store(store)


@router.post("/{provider}/test", response_model=InferenceProviderTestResult)
async def test_connection(
    provider: str, request: Request,
) -> InferenceProviderTestResult:
    schema = _validate_provider(provider)
    pid = _project_scope(request)
    async with _LOCK:
        store = _read_store()
        idx, row = _find_row(store, provider, pid)
    if row is None or not row.get("secrets"):
        return InferenceProviderTestResult(
            ok=False,
            detail="No credentials configured for this provider yet.",
            provider=provider,
            tested_at=datetime.now(timezone.utc).isoformat(),
        )

    # Decrypt
    try:
        crypto = _crypto()
        plain_secrets = {k: crypto.decrypt(v) for k, v in row["secrets"].items()}
    except (MasterKeyMissing, SecretCorrupted) as exc:
        return InferenceProviderTestResult(
            ok=False,
            detail=f"Decrypt failed: {exc}",
            provider=provider,
            tested_at=datetime.now(timezone.utc).isoformat(),
        )

    env = row.get("env") or {}
    result = await _dispatch_test(provider, plain_secrets, env)

    # Persist the test outcome alongside the credential row
    async with _LOCK:
        store2 = _read_store()
        idx2, row2 = _find_row(store2, provider, pid)
        if row2 is not None:
            row2["last_tested_at"] = result.tested_at
            row2["last_test_ok"] = result.ok
            row2["last_test_detail"] = result.detail
            store2["providers"][idx2] = row2
            _write_store(store2)

    # Honour the schema's required-key check: warn if not all required
    # keys are set, even if the provider responded.
    missing = [k for k in schema["secret_keys"] if k not in plain_secrets]
    if missing and result.ok:
        result = InferenceProviderTestResult(
            ok=False,
            detail=(
                f"Connection probe succeeded but the following required "
                f"keys are missing: {', '.join(missing)}."
            ),
            provider=provider,
            tested_at=result.tested_at,
        )
    return result


# ── Test-connection dispatch ────────────────────────────────────────


async def _dispatch_test(
    provider: str, secrets: dict[str, str], env: dict[str, str],
) -> InferenceProviderTestResult:
    now = datetime.now(timezone.utc).isoformat()
    try:
        if provider == "runpod":
            return await _test_runpod(secrets, now)
        if provider == "modal":
            return await _test_modal(secrets, now)
        if provider == "vastai":
            return await _test_vastai(secrets, now)
        if provider == "colab":
            return _test_colab(secrets, now)
        if provider == "custom":
            return await _test_custom(secrets, env, now)
    except Exception as exc:  # noqa: BLE001 — bubble all probe errors as ok=False
        return InferenceProviderTestResult(
            ok=False,
            detail=f"Test connection raised: {type(exc).__name__}: {exc}",
            provider=provider,
            tested_at=now,
        )
    return InferenceProviderTestResult(
        ok=False,
        detail=f"No probe registered for provider {provider!r}.",
        provider=provider,
        tested_at=now,
    )


def _httpx_or_none():
    """Return the httpx module if installed, else None.

    httpx is in the SDK's dependency tree (litellm pulls it), but we
    degrade cleanly so the admin still serves if it's missing.
    """
    try:
        import httpx
        return httpx
    except ImportError:
        return None


async def _test_runpod(
    secrets: dict[str, str], now: str,
) -> InferenceProviderTestResult:
    api_key = secrets.get("RUNPOD_API_KEY", "").strip()
    if not api_key:
        return InferenceProviderTestResult(
            ok=False, detail="RUNPOD_API_KEY is empty.",
            provider="runpod", tested_at=now,
        )
    httpx = _httpx_or_none()
    if httpx is None:
        return InferenceProviderTestResult(
            ok=False,
            detail=(
                "httpx not installed; verify manually with "
                "`runpodctl whoami` (CLI) or `curl -X POST -H \"Authorization: Bearer $RUNPOD_API_KEY\" https://api.runpod.io/graphql -d '{\"query\":\"{myself{id email}}\"}'`."
            ),
            provider="runpod", tested_at=now,
        )
    # RunPod GraphQL whoami
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.runpod.io/graphql",
            json={"query": "{myself{id email}}"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if resp.status_code != 200:
        return InferenceProviderTestResult(
            ok=False,
            detail=f"RunPod GraphQL HTTP {resp.status_code}: {resp.text[:200]}",
            provider="runpod", tested_at=now,
        )
    data = resp.json()
    if "errors" in data and data["errors"]:
        return InferenceProviderTestResult(
            ok=False,
            detail=f"RunPod auth rejected: {data['errors'][0].get('message', 'unknown')}",
            provider="runpod", tested_at=now,
        )
    me = (data.get("data") or {}).get("myself") or {}
    if not me.get("id"):
        return InferenceProviderTestResult(
            ok=False,
            detail="RunPod returned no `myself` payload — token may be invalid.",
            provider="runpod", tested_at=now,
        )
    return InferenceProviderTestResult(
        ok=True,
        detail=f"Authenticated as RunPod user {me.get('email') or me['id']}.",
        provider="runpod", tested_at=now,
    )


async def _test_modal(
    secrets: dict[str, str], now: str,
) -> InferenceProviderTestResult:
    token_id = secrets.get("MODAL_TOKEN_ID", "").strip()
    token_secret = secrets.get("MODAL_TOKEN_SECRET", "").strip()
    if not (token_id and token_secret):
        return InferenceProviderTestResult(
            ok=False,
            detail="Both MODAL_TOKEN_ID and MODAL_TOKEN_SECRET are required.",
            provider="modal", tested_at=now,
        )
    # Modal's auth/transport is gRPC over a private control plane; the
    # honest cheap check is "the SDK accepts the credentials". We
    # delegate to the SDK if it's installed; otherwise we surface a
    # shape-check (plausible token IDs start with `ak-`) and tell the
    # operator to verify with `modal token current`.
    try:
        from modal.config import _store_user_config  # noqa: F401  # presence check
    except ImportError:
        if token_id.startswith("ak-") and len(token_secret) >= 32:
            return InferenceProviderTestResult(
                ok=False,
                detail=(
                    "modal SDK not installed — token shape looks "
                    "plausible. Verify with `modal token current` "
                    "after running `pip install modal`."
                ),
                provider="modal", tested_at=now,
            )
        return InferenceProviderTestResult(
            ok=False,
            detail=(
                "modal SDK not installed and token shape is unexpected "
                "(MODAL_TOKEN_ID should start with `ak-`). "
                "Install with `pip install modal` and re-test."
            ),
            provider="modal", tested_at=now,
        )

    # SDK present — drive a `modal.config` lookup that fails cleanly
    # if the credentials are wrong.
    try:
        from modal.client import _Client  # type: ignore[attr-defined]
    except ImportError:
        return InferenceProviderTestResult(
            ok=False,
            detail=(
                "modal SDK installed but the internal _Client API is "
                "not importable on this version. Verify manually with "
                "`modal token current`."
            ),
            provider="modal", tested_at=now,
        )

    # Set the env vars temporarily and instantiate the client. This
    # round-trips a real auth call to Modal's control plane.
    prev_id = os.environ.get("MODAL_TOKEN_ID")
    prev_secret = os.environ.get("MODAL_TOKEN_SECRET")
    os.environ["MODAL_TOKEN_ID"] = token_id
    os.environ["MODAL_TOKEN_SECRET"] = token_secret
    try:
        client = await _Client.from_env()  # type: ignore[attr-defined]
        await client.close()
    except Exception as exc:  # noqa: BLE001
        return InferenceProviderTestResult(
            ok=False,
            detail=f"Modal SDK rejected credentials: {exc}",
            provider="modal", tested_at=now,
        )
    finally:
        if prev_id is None:
            os.environ.pop("MODAL_TOKEN_ID", None)
        else:
            os.environ["MODAL_TOKEN_ID"] = prev_id
        if prev_secret is None:
            os.environ.pop("MODAL_TOKEN_SECRET", None)
        else:
            os.environ["MODAL_TOKEN_SECRET"] = prev_secret
    return InferenceProviderTestResult(
        ok=True,
        detail="Modal SDK accepted the token pair.",
        provider="modal", tested_at=now,
    )


async def _test_vastai(
    secrets: dict[str, str], now: str,
) -> InferenceProviderTestResult:
    api_key = secrets.get("VASTAI_API_KEY", "").strip()
    if not api_key:
        return InferenceProviderTestResult(
            ok=False, detail="VASTAI_API_KEY is empty.",
            provider="vastai", tested_at=now,
        )
    httpx = _httpx_or_none()
    if httpx is None:
        return InferenceProviderTestResult(
            ok=False,
            detail=(
                "httpx not installed; verify manually with "
                "`vastai show user`."
            ),
            provider="vastai", tested_at=now,
        )
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://console.vast.ai/api/v0/users/current/",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if resp.status_code == 401:
        return InferenceProviderTestResult(
            ok=False,
            detail="Vast.ai rejected the API key (HTTP 401).",
            provider="vastai", tested_at=now,
        )
    if resp.status_code != 200:
        return InferenceProviderTestResult(
            ok=False,
            detail=f"Vast.ai HTTP {resp.status_code}: {resp.text[:200]}",
            provider="vastai", tested_at=now,
        )
    data = resp.json() or {}
    user = data.get("username") or data.get("email") or data.get("id")
    return InferenceProviderTestResult(
        ok=True,
        detail=f"Authenticated as Vast.ai user {user or '(no username on payload)'}.",
        provider="vastai", tested_at=now,
    )


def _test_colab(
    secrets: dict[str, str], now: str,
) -> InferenceProviderTestResult:
    # Colab itself has no whoami API — Example 44's orchestration
    # works via Drive OAuth. The most we can verify in-process is that
    # the supplied JSON parses as a Google OAuth client config.
    blob = secrets.get("GOOGLE_DRIVE_OAUTH_JSON", "").strip()
    if not blob:
        return InferenceProviderTestResult(
            ok=False,
            detail="GOOGLE_DRIVE_OAUTH_JSON is empty.",
            provider="colab", tested_at=now,
        )
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as exc:
        return InferenceProviderTestResult(
            ok=False,
            detail=f"OAuth JSON did not parse: {exc}",
            provider="colab", tested_at=now,
        )
    # Google OAuth client configs have either an `installed` or `web`
    # top-level key with `client_id` underneath.
    inner = parsed.get("installed") or parsed.get("web") or {}
    if not isinstance(inner, dict) or not inner.get("client_id"):
        return InferenceProviderTestResult(
            ok=False,
            detail=(
                "OAuth JSON is missing `installed.client_id` or "
                "`web.client_id` — re-download from the Google Cloud "
                "Console (OAuth client → Desktop app)."
            ),
            provider="colab", tested_at=now,
        )
    return InferenceProviderTestResult(
        ok=True,
        detail=(
            f"OAuth client config parsed (client_id "
            f"{inner['client_id'][:20]}…). The first orchestrated run "
            f"will complete the device-code flow against Drive."
        ),
        provider="colab", tested_at=now,
    )


async def _test_custom(
    secrets: dict[str, str], env: dict[str, str], now: str,
) -> InferenceProviderTestResult:
    base_url = (env.get("CUSTOM_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        return InferenceProviderTestResult(
            ok=False,
            detail="CUSTOM_BASE_URL is required.",
            provider="custom", tested_at=now,
        )
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return InferenceProviderTestResult(
            ok=False,
            detail=f"CUSTOM_BASE_URL must start with http:// or https://; got {base_url!r}.",
            provider="custom", tested_at=now,
        )
    auth_shape = (env.get("CUSTOM_AUTH_SHAPE") or "none").lower()
    auth_value = secrets.get("CUSTOM_AUTH_VALUE", "").strip()

    httpx = _httpx_or_none()
    if httpx is None:
        return InferenceProviderTestResult(
            ok=False,
            detail="httpx not installed; verify manually with curl.",
            provider="custom", tested_at=now,
        )

    headers: dict[str, str] = {}
    auth: Any = None
    if auth_shape == "bearer":
        if not auth_value:
            return InferenceProviderTestResult(
                ok=False,
                detail="bearer auth selected but CUSTOM_AUTH_VALUE is empty.",
                provider="custom", tested_at=now,
            )
        headers["Authorization"] = f"Bearer {auth_value}"
    elif auth_shape == "basic":
        if ":" not in auth_value:
            return InferenceProviderTestResult(
                ok=False,
                detail="basic auth expects CUSTOM_AUTH_VALUE in `user:password` form.",
                provider="custom", tested_at=now,
            )
        user, _, password = auth_value.partition(":")
        auth = httpx.BasicAuth(user, password)
    elif auth_shape == "sigv4":
        return InferenceProviderTestResult(
            ok=False,
            detail=(
                "SigV4 verification is not in v1.0 — install botocore "
                "and verify manually. The credential is stored "
                "encrypted regardless."
            ),
            provider="custom", tested_at=now,
        )
    elif auth_shape != "none":
        return InferenceProviderTestResult(
            ok=False,
            detail=f"Unknown auth_shape {auth_shape!r}; expected one of {_AUTH_SHAPES}.",
            provider="custom", tested_at=now,
        )

    # Probe order: try /v1/models (OpenAI-shaped), then a HEAD on the
    # base URL, then a GET on the base URL. The first 2xx wins.
    candidates = [
        ("GET", f"{base_url}/v1/models"),
        ("HEAD", base_url),
        ("GET", base_url),
    ]
    last_status: int | None = None
    last_body: str = ""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for method, url in candidates:
            try:
                resp = await client.request(
                    method, url, headers=headers, auth=auth,
                )
            except Exception as exc:  # noqa: BLE001
                return InferenceProviderTestResult(
                    ok=False,
                    detail=f"{method} {url} raised: {exc}",
                    provider="custom", tested_at=now,
                )
            last_status = resp.status_code
            last_body = resp.text[:200] if resp.content else ""
            if 200 <= resp.status_code < 300:
                model_hint = ""
                if url.endswith("/v1/models"):
                    try:
                        payload = resp.json()
                        models = (payload.get("data") or [])
                        if models:
                            model_hint = (
                                f" Models advertised: "
                                f"{', '.join(m.get('id', '?') for m in models[:3])}."
                            )
                    except Exception:  # noqa: BLE001
                        pass
                return InferenceProviderTestResult(
                    ok=True,
                    detail=f"{method} {url} → {resp.status_code}.{model_hint}",
                    provider="custom", tested_at=now,
                )
    return InferenceProviderTestResult(
        ok=False,
        detail=(
            f"All probes failed; last response was HTTP {last_status}: "
            f"{last_body or '(empty body)'}"
        ),
        provider="custom", tested_at=now,
    )


# ── Wiring ──────────────────────────────────────────────────────────


def register(app: FastAPI) -> None:
    """Mount routes under /api/v1/admin/inference-providers/*."""
    app.include_router(router)
