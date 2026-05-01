# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for SavedWorkflowStore (in-memory implementation)."""

import pytest

from sagewai.admin.workflow_store import InMemorySavedWorkflowStore, SavedWorkflow


@pytest.fixture
def store():
    return InMemorySavedWorkflowStore()


@pytest.mark.asyncio
class TestInMemorySavedWorkflowStore:
    async def test_save_new_workflow(self, store):
        wf_id = await store.save(
            name="test-pipeline",
            yaml_content="name: test-pipeline\nagents: {}",
            description="A test workflow",
        )
        assert wf_id
        wf = await store.get(wf_id)
        assert wf is not None
        assert wf.name == "test-pipeline"
        assert wf.version == 1
        assert wf.is_active is True

    async def test_save_updates_existing(self, store):
        wf_id = await store.save(
            name="test",
            yaml_content="version1",
        )
        wf_id2 = await store.save(
            name="test",
            yaml_content="version2",
        )
        assert wf_id == wf_id2
        wf = await store.get(wf_id)
        assert wf.version == 2
        assert wf.yaml_content == "version2"

    async def test_get_by_name(self, store):
        await store.save(name="my-wf", yaml_content="content")
        wf = await store.get_by_name("my-wf")
        assert wf is not None
        assert wf.name == "my-wf"

    async def test_get_by_name_not_found(self, store):
        wf = await store.get_by_name("nonexistent")
        assert wf is None

    async def test_list_workflows(self, store):
        await store.save(name="wf-1", yaml_content="c1")
        await store.save(name="wf-2", yaml_content="c2")
        items = await store.list()
        assert len(items) == 2

    async def test_list_with_search(self, store):
        await store.save(name="research-pipeline", yaml_content="c1", description="Research")
        await store.save(name="data-pipeline", yaml_content="c2", description="Data processing")
        items = await store.list(search="research")
        assert len(items) == 1
        assert items[0].name == "research-pipeline"

    async def test_delete_workflow(self, store):
        wf_id = await store.save(name="to-delete", yaml_content="c")
        result = await store.delete(wf_id)
        assert result is True
        wf = await store.get_by_name("to-delete")
        assert wf is None  # get_by_name filters inactive

    async def test_version_history(self, store):
        wf_id = await store.save(name="versioned", yaml_content="v1")
        await store.save(name="versioned", yaml_content="v2")
        await store.save(name="versioned", yaml_content="v3")

        versions = await store.list_versions(wf_id)
        assert len(versions) == 3

    async def test_get_specific_version(self, store):
        wf_id = await store.save(name="versioned", yaml_content="v1-content")
        await store.save(name="versioned", yaml_content="v2-content")

        v1 = await store.get_version(wf_id, 1)
        assert v1 == "v1-content"
        v2 = await store.get_version(wf_id, 2)
        assert v2 == "v2-content"

    async def test_count(self, store):
        assert await store.count() == 0
        await store.save(name="a", yaml_content="c")
        await store.save(name="b", yaml_content="c")
        assert await store.count() == 2

    async def test_project_isolation(self, store):
        await store.save(name="wf", yaml_content="c", project_id="proj-a")
        await store.save(name="wf", yaml_content="c", project_id="proj-b")
        items_a = await store.list(project_id="proj-a")
        items_b = await store.list(project_id="proj-b")
        assert len(items_a) == 1
        assert len(items_b) == 1
