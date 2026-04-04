"""Tests for distributed worker routing and credential injection.

Tests cover:
- Data model validation (WorkerCredentials, RoutingConstraints, RoutingStrategy)
- ContextVar credential injection (get_worker_credentials)
- Load balancer strategies (round-robin, least-loaded, threshold)
- Backward compatibility (workers without routing claim unrouted runs)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sagewai.models.inference import InferenceParams
from sagewai.models.worker import (
    RoutingConstraints,
    RoutingStrategy,
    WorkerCredentials,
)

# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestWorkerCredentials:
    """Test WorkerCredentials model."""

    def test_defaults(self) -> None:
        creds = WorkerCredentials()
        assert creds.model_overrides == {}
        assert creds.inference_overrides is None
        assert creds.env_overrides == {}

    def test_model_overrides(self) -> None:
        creds = WorkerCredentials(model_overrides={"default": "ollama/llama3.2"})
        assert creds.model_overrides["default"] == "ollama/llama3.2"

    def test_inference_overrides(self) -> None:
        creds = WorkerCredentials(
            inference_overrides=InferenceParams(
                api_base="http://localhost:11434",
                api_key="sk-test-123",
            )
        )
        assert creds.inference_overrides is not None
        assert creds.inference_overrides.api_base == "http://localhost:11434"
        assert creds.inference_overrides.api_key == "sk-test-123"

    def test_full_config(self) -> None:
        creds = WorkerCredentials(
            model_overrides={"default": "ollama/llama3.2", "fast": "ollama/phi3"},
            inference_overrides=InferenceParams(
                api_base="http://localhost:11434",
            ),
            env_overrides={"CUDA_VISIBLE_DEVICES": "0"},
        )
        assert len(creds.model_overrides) == 2
        assert creds.env_overrides["CUDA_VISIBLE_DEVICES"] == "0"


class TestRoutingConstraints:
    """Test RoutingConstraints model."""

    def test_defaults(self) -> None:
        rc = RoutingConstraints()
        assert rc.worker_pool is None
        assert rc.worker_labels is None
        assert rc.worker_id is None
        assert rc.strategy == RoutingStrategy.LEAST_LOADED
        assert rc.capacity_threshold == 0.9

    def test_direct_routing(self) -> None:
        rc = RoutingConstraints(
            worker_pool="local-ollama",
            strategy=RoutingStrategy.DIRECT,
        )
        assert rc.worker_pool == "local-ollama"
        assert rc.strategy == RoutingStrategy.DIRECT

    def test_threshold_routing(self) -> None:
        rc = RoutingConstraints(
            worker_pool="cloud-gpt4",
            strategy=RoutingStrategy.THRESHOLD,
            capacity_threshold=0.8,
        )
        assert rc.capacity_threshold == 0.8

    def test_capacity_threshold_validation(self) -> None:
        with pytest.raises(ValueError):
            RoutingConstraints(capacity_threshold=1.5)
        with pytest.raises(ValueError):
            RoutingConstraints(capacity_threshold=-0.1)

    def test_label_routing(self) -> None:
        rc = RoutingConstraints(
            worker_labels={"zone": "eu", "gpu": True},
        )
        assert rc.worker_labels == {"zone": "eu", "gpu": True}


class TestRoutingStrategy:
    """Test RoutingStrategy enum."""

    def test_values(self) -> None:
        assert RoutingStrategy.DIRECT == "direct"
        assert RoutingStrategy.ROUND_ROBIN == "round_robin"
        assert RoutingStrategy.LEAST_LOADED == "least_loaded"
        assert RoutingStrategy.THRESHOLD == "threshold"

    def test_from_string(self) -> None:
        assert RoutingStrategy("direct") == RoutingStrategy.DIRECT
        assert RoutingStrategy("round_robin") == RoutingStrategy.ROUND_ROBIN


# ---------------------------------------------------------------------------
# ContextVar credential injection tests
# ---------------------------------------------------------------------------


class TestWorkerCredentialInjection:
    """Test the ContextVar-based credential injection mechanism."""

    def test_default_is_none(self) -> None:
        from sagewai.core.worker import get_worker_credentials

        assert get_worker_credentials() is None

    def test_set_and_get(self) -> None:
        from sagewai.core.worker import (
            _worker_credentials,
            get_worker_credentials,
        )

        creds = WorkerCredentials(
            model_overrides={"default": "ollama/llama3.2"},
        )
        token = _worker_credentials.set(creds)
        try:
            result = get_worker_credentials()
            assert result is not None
            assert result.model_overrides["default"] == "ollama/llama3.2"
        finally:
            _worker_credentials.reset(token)

        # After reset, should be None again
        assert get_worker_credentials() is None

    @pytest.mark.asyncio
    async def test_isolation_between_tasks(self) -> None:
        """ContextVar isolation: setting creds in one scope doesn't leak."""
        from sagewai.core.worker import (
            _worker_credentials,
            get_worker_credentials,
        )

        creds_a = WorkerCredentials(model_overrides={"default": "model-a"})
        creds_b = WorkerCredentials(model_overrides={"default": "model-b"})

        # Set creds_a, verify, reset, set creds_b, verify
        token_a = _worker_credentials.set(creds_a)
        c = get_worker_credentials()
        assert c is not None and c.model_overrides["default"] == "model-a"
        _worker_credentials.reset(token_a)

        token_b = _worker_credentials.set(creds_b)
        c = get_worker_credentials()
        assert c is not None and c.model_overrides["default"] == "model-b"
        _worker_credentials.reset(token_b)

        # After reset, should be None
        assert get_worker_credentials() is None


# ---------------------------------------------------------------------------
# Load balancer tests
# ---------------------------------------------------------------------------


class TestLoadBalancer:
    """Test WorkerLoadBalancer strategies."""

    @pytest.fixture
    def mock_store(self) -> MagicMock:
        store = MagicMock()
        store._pool = AsyncMock()
        return store

    @pytest.mark.asyncio
    async def test_direct_returns_none(self, mock_store: MagicMock) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        balancer = WorkerLoadBalancer(mock_store)
        result = await balancer.assign(RoutingConstraints(strategy=RoutingStrategy.DIRECT))
        assert result is None

    @pytest.mark.asyncio
    async def test_explicit_worker_id_returned(self, mock_store: MagicMock) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        balancer = WorkerLoadBalancer(mock_store)
        result = await balancer.assign(RoutingConstraints(worker_id="specific-worker:1234"))
        assert result == "specific-worker:1234"

    @pytest.mark.asyncio
    async def test_round_robin(self, mock_store: MagicMock) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        workers = [
            {
                "worker_id": "w1",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 1,
                "load_ratio": 0.25,
            },
            {
                "worker_id": "w2",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 2,
                "load_ratio": 0.50,
            },
            {
                "worker_id": "w3",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 0,
                "load_ratio": 0.0,
            },
        ]
        mock_store._pool.fetch = AsyncMock(
            return_value=[
                MagicMock(**{k: v for k, v in w.items()}, __getitem__=lambda s, k: getattr(s, k))
                for w in workers
            ]
        )

        balancer = WorkerLoadBalancer(mock_store)

        # Mock _get_eligible_workers to return our test data
        balancer._get_eligible_workers = AsyncMock(return_value=workers)

        constraints = RoutingConstraints(strategy=RoutingStrategy.ROUND_ROBIN)

        results = []
        for _ in range(6):
            r = await balancer.assign(constraints)
            results.append(r)

        # Should cycle through w1, w2, w3, w1, w2, w3
        assert results == ["w1", "w2", "w3", "w1", "w2", "w3"]

    @pytest.mark.asyncio
    async def test_least_loaded(self, mock_store: MagicMock) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        workers = [
            {
                "worker_id": "w1",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 3,
                "load_ratio": 0.75,
            },
            {
                "worker_id": "w2",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 1,
                "load_ratio": 0.25,
            },
            {
                "worker_id": "w3",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 2,
                "load_ratio": 0.50,
            },
        ]

        balancer = WorkerLoadBalancer(mock_store)
        balancer._get_eligible_workers = AsyncMock(return_value=workers)

        result = await balancer.assign(RoutingConstraints(strategy=RoutingStrategy.LEAST_LOADED))
        assert result == "w2"  # Lowest load ratio

    @pytest.mark.asyncio
    async def test_threshold_filters_overloaded(self, mock_store: MagicMock) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        workers = [
            {
                "worker_id": "w1",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 4,
                "load_ratio": 1.0,
            },
            {
                "worker_id": "w2",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 3,
                "load_ratio": 0.75,
            },
            {
                "worker_id": "w3",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 1,
                "load_ratio": 0.25,
            },
        ]

        balancer = WorkerLoadBalancer(mock_store)
        balancer._get_eligible_workers = AsyncMock(return_value=workers)

        result = await balancer.assign(
            RoutingConstraints(
                strategy=RoutingStrategy.THRESHOLD,
                capacity_threshold=0.9,
            )
        )
        # w1 (1.0) excluded, w2 (0.75) and w3 (0.25) eligible, w3 picked as least loaded
        assert result == "w3"

    @pytest.mark.asyncio
    async def test_threshold_fallback_when_all_overloaded(self, mock_store: MagicMock) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        workers = [
            {
                "worker_id": "w1",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 4,
                "load_ratio": 1.0,
            },
            {
                "worker_id": "w2",
                "pool": "p",
                "max_concurrent": 4,
                "active_runs": 4,
                "load_ratio": 1.0,
            },
        ]

        balancer = WorkerLoadBalancer(mock_store)
        balancer._get_eligible_workers = AsyncMock(return_value=workers)

        result = await balancer.assign(
            RoutingConstraints(
                strategy=RoutingStrategy.THRESHOLD,
                capacity_threshold=0.9,
            )
        )
        # All above threshold — falls back to least loaded (either w1 or w2)
        assert result in ("w1", "w2")

    @pytest.mark.asyncio
    async def test_no_eligible_workers(self, mock_store: MagicMock) -> None:
        from sagewai.core.load_balancer import WorkerLoadBalancer

        balancer = WorkerLoadBalancer(mock_store)
        balancer._get_eligible_workers = AsyncMock(return_value=[])

        result = await balancer.assign(
            RoutingConstraints(
                worker_pool="nonexistent",
                strategy=RoutingStrategy.LEAST_LOADED,
            )
        )
        assert result is None  # Falls back to claim-time routing


# ---------------------------------------------------------------------------
# WorkflowWorker integration tests (unit-level with mocks)
# ---------------------------------------------------------------------------


class TestWorkerConstructor:
    """Test WorkflowWorker constructor with new routing params."""

    def test_default_pool(self) -> None:
        from sagewai.core.worker import WorkflowWorker

        store = MagicMock()
        w = WorkflowWorker(store=store, workflow_registry={})
        assert w._pool == "default"
        assert w._labels == {}
        assert w._credentials is None

    def test_custom_pool_and_labels(self) -> None:
        from sagewai.core.worker import WorkflowWorker

        store = MagicMock()
        creds = WorkerCredentials(
            model_overrides={"default": "ollama/llama3.2"},
        )
        w = WorkflowWorker(
            store=store,
            workflow_registry={},
            pool="local-ollama",
            labels={"zone": "local", "gpu": True},
            credentials=creds,
        )
        assert w._pool == "local-ollama"
        assert w._labels == {"zone": "local", "gpu": True}
        assert w._credentials is not None
        assert w._credentials.model_overrides["default"] == "ollama/llama3.2"

    def test_project_scoped_worker(self) -> None:
        """Worker scoped to a project (like Temporal namespace)."""
        from sagewai.core.worker import WorkflowWorker

        store = MagicMock()
        w = WorkflowWorker(
            store=store,
            workflow_registry={},
            project_id="acme-corp",
            pool="cloud-gpt4",
        )
        assert w._project_id == "acme-corp"
        assert w._pool == "cloud-gpt4"


# ---------------------------------------------------------------------------
# UniversalAgent credential injection test
# ---------------------------------------------------------------------------


class TestUniversalAgentCredentialInjection:
    """Test that UniversalAgent reads worker credentials from ContextVar."""

    def test_no_credentials_no_override(self) -> None:
        """Without worker credentials, kwargs are unchanged."""
        from sagewai.core.worker import _worker_credentials

        # Ensure no credentials are set
        assert _worker_credentials.get() is None

        from sagewai.engines.universal import UniversalAgent

        agent = UniversalAgent(name="test", model="gpt-4o")

        kwargs = agent._build_litellm_kwargs(
            [{"role": "user", "content": "hello"}],
            [],
        )
        assert kwargs["model"] == "gpt-4o"

    def test_worker_credentials_override_model(self) -> None:
        """Worker credentials should override the agent's default model."""
        from sagewai.core.worker import _worker_credentials
        from sagewai.engines.universal import UniversalAgent

        creds = WorkerCredentials(
            model_overrides={"default": "ollama/llama3.2"},
            inference_overrides=InferenceParams(
                api_base="http://localhost:11434",
            ),
        )
        token = _worker_credentials.set(creds)
        try:
            agent = UniversalAgent(name="test", model="gpt-4o")

            kwargs = agent._build_litellm_kwargs(
                [{"role": "user", "content": "hello"}],
                [],
            )
            assert kwargs["model"] == "ollama/llama3.2"
            assert kwargs["api_base"] == "http://localhost:11434"
        finally:
            _worker_credentials.reset(token)

    def test_explicit_model_override_wins(self) -> None:
        """An explicit model_override param should not be overridden by worker creds."""
        from sagewai.core.worker import _worker_credentials
        from sagewai.engines.universal import UniversalAgent

        creds = WorkerCredentials(
            model_overrides={"default": "ollama/llama3.2"},
        )
        token = _worker_credentials.set(creds)
        try:
            agent = UniversalAgent(name="test", model="gpt-4o")

            kwargs = agent._build_litellm_kwargs(
                [{"role": "user", "content": "hello"}],
                [],
                model_override="claude-sonnet-4-6",
            )
            # Explicit model_override should win over worker credentials
            assert kwargs["model"] == "claude-sonnet-4-6"
        finally:
            _worker_credentials.reset(token)

    def test_worker_api_key_override(self) -> None:
        """Worker credentials should override api_key."""
        from sagewai.core.worker import _worker_credentials
        from sagewai.engines.universal import UniversalAgent

        creds = WorkerCredentials(
            inference_overrides=InferenceParams(
                api_key="sk-worker-secret",
            ),
        )
        token = _worker_credentials.set(creds)
        try:
            agent = UniversalAgent(
                name="test",
                model="gpt-4o",
                api_key="sk-original",
            )

            kwargs = agent._build_litellm_kwargs(
                [{"role": "user", "content": "hello"}],
                [],
            )
            # Worker credentials override agent config
            assert kwargs["api_key"] == "sk-worker-secret"
        finally:
            _worker_credentials.reset(token)
