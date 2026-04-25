# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for workflow.enqueue sandbox requirement threading."""
import pytest

from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxMode,
)


@pytest.fixture
def mock_store():
    class _MockStore:
        def __init__(self):
            self.last_saved_run = None

        async def save_run(self, run):
            self.last_saved_run = run

        async def load_run(self, *_):
            return None

        async def get_project_defaults(self, *_):
            return None

    return _MockStore()


@pytest.mark.asyncio
async def test_enqueue_explicit_requirements_persist(mock_store):
    """Caller passes explicit requirements; they land on the stored run."""
    from sagewai.core.state import DurableWorkflow

    wf = DurableWorkflow(name="wf", store=mock_store)
    run_id = await wf.enqueue(
        input_data={"x": 1},
        requires_sandbox_mode=SandboxMode.PER_RUN,
        requires_image="ghcr.io/sagewai/sandbox-ml:0.1.5",
        requires_network_policy=NetworkPolicy.FULL,
    )
    saved = mock_store.last_saved_run
    assert saved is not None
    assert saved.run_id == run_id
    assert saved.requires_sandbox_mode is SandboxMode.PER_RUN
    assert saved.requires_image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
    assert saved.requires_network_policy is NetworkPolicy.FULL


@pytest.mark.asyncio
async def test_enqueue_falls_through_to_sdk_default(mock_store, caplog):
    """No explicit, no project defaults → SDK default with WARN logs."""
    import logging

    from sagewai.core.state import DurableWorkflow

    wf = DurableWorkflow(name="wf", store=mock_store)
    with caplog.at_level(logging.WARNING, logger="sagewai.sandbox.resolution"):
        await wf.enqueue(input_data={"x": 1})
    saved = mock_store.last_saved_run
    assert saved.requires_sandbox_mode is SandboxMode.NONE
    # Three WARN entries — one per field that fell through
    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    assert sum("sandbox resolution" in r.message for r in warns) == 3
