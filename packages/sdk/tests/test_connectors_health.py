# packages/sagewai/tests/test_connectors_health.py
import pytest
from sagewai.connectors.health import HealthMonitor


def test_circuit_breaker_healthy_initially():
    monitor = HealthMonitor()
    status = monitor.status("slack")
    assert status.status == "disconnected"  # unknown = disconnected


def test_circuit_breaker_degrades_after_failures():
    monitor = HealthMonitor()
    for _ in range(3):
        monitor._record_failure("slack")
    assert monitor.status("slack").status == "degraded"


def test_circuit_breaker_disconnects_after_many_failures():
    monitor = HealthMonitor()
    for _ in range(10):
        monitor._record_failure("slack")
    assert monitor.status("slack").status == "disconnected"


def test_circuit_breaker_resets_on_success():
    monitor = HealthMonitor()
    for _ in range(5):
        monitor._record_failure("slack")
    assert monitor.status("slack").status == "degraded"
    monitor._record_success("slack", latency_ms=42, tool_count=5)
    assert monitor.status("slack").status == "healthy"


@pytest.mark.asyncio
async def test_health_monitor_triggers_reconnect():
    from unittest.mock import AsyncMock, MagicMock
    from sagewai.connectors.registry import ConnectorRegistry
    from sagewai.connectors.base import HealthStatus as HS

    mock_connector = MagicMock()
    mock_connector.name = "test"
    mock_connector.auth_fields = []
    mock_connector.health_check = AsyncMock(
        return_value=HS(status="healthy", tool_count=3),
    )

    reg = ConnectorRegistry()
    reg._connectors["test"] = mock_connector

    monitor = HealthMonitor()
    await monitor._check_all_with_reconnect(reg)
    assert monitor.status("test").status == "healthy"
