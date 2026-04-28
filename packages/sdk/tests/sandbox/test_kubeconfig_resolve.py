"""Unit tests for k8s_client.make_api_client (kubeconfig resolution chain)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("kubernetes_asyncio")


@pytest.mark.asyncio
async def test_explicit_path_wins(tmp_path):
    from sagewai.sandbox.k8s_client import make_api_client

    cfg = tmp_path / "kubeconfig"
    cfg.write_text("apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n")
    with patch("kubernetes_asyncio.config.load_kube_config", new=AsyncMock()) as load_kube:
        await make_api_client(
            kubeconfig_path=str(cfg), use_in_cluster=False, default_path=tmp_path / "ignored",
        )
    load_kube.assert_called_once_with(config_file=str(cfg))


@pytest.mark.asyncio
async def test_in_cluster_used_when_token_exists(tmp_path, monkeypatch):
    from sagewai.sandbox.k8s_client import _IN_CLUSTER_TOKEN_PATH, make_api_client

    fake_token = tmp_path / "token"
    fake_token.write_text("xx")
    monkeypatch.setattr("sagewai.sandbox.k8s_client._IN_CLUSTER_TOKEN_PATH", fake_token)
    with patch("kubernetes_asyncio.config.load_incluster_config") as load_in:
        await make_api_client(
            kubeconfig_path=None, use_in_cluster=True, default_path=tmp_path / "missing",
        )
    load_in.assert_called_once()


@pytest.mark.asyncio
async def test_default_path_fallback(tmp_path):
    from sagewai.sandbox.k8s_client import make_api_client

    cfg = tmp_path / "default-kubeconfig"
    cfg.write_text("apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n")
    with patch("kubernetes_asyncio.config.load_kube_config", new=AsyncMock()) as load_kube:
        await make_api_client(
            kubeconfig_path=None, use_in_cluster=False, default_path=cfg,
        )
    load_kube.assert_called_once_with(config_file=str(cfg))


@pytest.mark.asyncio
async def test_no_config_raises(tmp_path, monkeypatch):
    from sagewai.sandbox.docker_backend import SandboxError
    from sagewai.sandbox.k8s_client import make_api_client

    monkeypatch.setattr(
        "sagewai.sandbox.k8s_client._IN_CLUSTER_TOKEN_PATH", tmp_path / "no-token",
    )
    with pytest.raises(SandboxError, match="no kubeconfig found"):
        await make_api_client(
            kubeconfig_path=None, use_in_cluster=True, default_path=tmp_path / "missing",
        )
