"""Shared test fixtures for sagewai SDK tests."""

import socket
from pathlib import Path

import pytest

# Load .env file (project root or packages/sagewai/) so integration tests
# have access to API keys and database URLs without manual exporting.
try:
    from dotenv import load_dotenv

    # Walk up from tests/ to find the nearest .env
    _tests_dir = Path(__file__).resolve().parent
    for _candidate in [_tests_dir.parent, _tests_dir.parent.parent.parent]:
        _env_file = _candidate / ".env"
        if _env_file.exists():
            load_dotenv(_env_file, override=True)
            break
except ImportError:
    pass

from sagewai.core.base import BaseAgent
from sagewai.models.message import ChatMessage


def _port_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection can be established."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Cache the check at import time so we don't probe on every test item.
_POSTGRES_AVAILABLE = _port_reachable("localhost", 5432)


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked 'integration' when infrastructure is offline."""
    skip_integration = pytest.mark.skip(
        reason="Infrastructure not available (Postgres on localhost:5432 unreachable)"
    )
    for item in items:
        if "integration" in item.keywords and not _POSTGRES_AVAILABLE:
            item.add_marker(skip_integration)


class SimpleTestAgent(BaseAgent):
    """Reusable test agent with predetermined responses."""

    def __init__(self, responses=None, **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses or [ChatMessage.assistant("ok")])
        self._call_count = 0
        self.last_messages = []

    async def _invoke_llm(self, messages, tools, *, model_override=None):
        self.last_messages = list(messages)
        resp = self._responses[self._call_count % len(self._responses)]
        self._call_count += 1
        return resp


@pytest.fixture
def simple_agent():
    """Factory fixture for creating test agents."""

    def _factory(responses=None, **kwargs):
        defaults = {"name": "test-agent"}
        defaults.update(kwargs)
        return SimpleTestAgent(responses=responses, **defaults)

    return _factory
