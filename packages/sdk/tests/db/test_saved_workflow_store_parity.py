# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Parity tests for SavedWorkflowStore — runs against both SQLite and Postgres.

Exercises the full public surface of SavedWorkflowStore, mirroring
InMemorySavedWorkflowStore semantics as the behavioral reference.
"""

import pytest

from sagewai.admin.workflow_store import SavedWorkflow, SavedWorkflowStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(engine):
    return SavedWorkflowStore(engine=engine)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_connected(dialect_engine):
    """is_connected must be True immediately after construction."""
    store = _make_store(dialect_engine)
    assert store.is_connected is True


@pytest.mark.asyncio
async def test_save_new_and_get(dialect_engine):
    """save() creates a workflow; get() returns it with correct fields."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(
        name="research-pipeline",
        yaml_content="name: research-pipeline\nagents: {}",
        description="Multi-agent research workflow",
        project_id="proj-1",
    )
    assert wf_id

    wf = await store.get(wf_id, project_id="proj-1")
    assert wf is not None
    assert isinstance(wf, SavedWorkflow)
    assert wf.id == wf_id
    assert wf.name == "research-pipeline"
    assert wf.description == "Multi-agent research workflow"
    assert wf.yaml_content == "name: research-pipeline\nagents: {}"
    assert wf.version == 1
    assert wf.is_active is True
    assert wf.project_id == "proj-1"


@pytest.mark.asyncio
async def test_save_returns_same_id_on_update(dialect_engine):
    """Saving same name twice in same project returns the same workflow id."""
    store = _make_store(dialect_engine)
    id1 = await store.save(name="wf", yaml_content="v1", project_id="p")
    id2 = await store.save(name="wf", yaml_content="v2", project_id="p")
    assert id1 == id2


@pytest.mark.asyncio
async def test_update_bumps_version_and_yaml(dialect_engine):
    """Updating a workflow increments version and updates yaml_content."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="wf", yaml_content="v1", project_id="p")
    await store.save(name="wf", yaml_content="v2", description="updated", project_id="p")

    wf = await store.get(wf_id, project_id="p")
    assert wf.version == 2
    assert wf.yaml_content == "v2"
    assert wf.description == "updated"


@pytest.mark.asyncio
async def test_version_bump_is_atomic_and_old_versions_retained(dialect_engine):
    """Version rows accumulate; parent reflects latest; all old versions survive."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="versioned", yaml_content="v1-content", project_id="p")
    await store.save(name="versioned", yaml_content="v2-content", project_id="p")
    await store.save(name="versioned", yaml_content="v3-content", project_id="p")

    wf = await store.get(wf_id, project_id="p")
    assert wf.version == 3
    assert wf.yaml_content == "v3-content"

    versions = await store.list_versions(wf_id, project_id="p")
    assert len(versions) == 3
    # list_versions returns newest first (DESC)
    assert versions[0]["version"] == 3
    assert versions[2]["version"] == 1


@pytest.mark.asyncio
async def test_get_version_returns_correct_yaml(dialect_engine):
    """get_version() returns yaml_content for that specific version number."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="versioned", yaml_content="v1-content", project_id="p")
    await store.save(name="versioned", yaml_content="v2-content", project_id="p")

    v1 = await store.get_version(wf_id, 1, project_id="p")
    v2 = await store.get_version(wf_id, 2, project_id="p")
    assert v1 == "v1-content"
    assert v2 == "v2-content"


@pytest.mark.asyncio
async def test_get_version_missing_returns_none(dialect_engine):
    """get_version() returns None for non-existent version."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="wf", yaml_content="v1", project_id="p")
    assert await store.get_version(wf_id, 99, project_id="p") is None


@pytest.mark.asyncio
async def test_get_by_name(dialect_engine):
    """get_by_name() retrieves an active workflow by name."""
    store = _make_store(dialect_engine)
    await store.save(name="my-wf", yaml_content="content", project_id="p")
    wf = await store.get_by_name("my-wf", project_id="p")
    assert wf is not None
    assert wf.name == "my-wf"


@pytest.mark.asyncio
async def test_get_by_name_not_found(dialect_engine):
    """get_by_name() returns None for unknown workflow."""
    store = _make_store(dialect_engine)
    assert await store.get_by_name("nonexistent", project_id="p") is None


@pytest.mark.asyncio
async def test_get_missing_returns_none(dialect_engine):
    """get() returns None for unknown workflow id."""
    store = _make_store(dialect_engine)
    assert await store.get("nope", project_id="p") is None


@pytest.mark.asyncio
async def test_list_all(dialect_engine):
    """list() returns all workflows in a project."""
    store = _make_store(dialect_engine)
    await store.save(name="wf-1", yaml_content="c1", project_id="p")
    await store.save(name="wf-2", yaml_content="c2", project_id="p")
    items = await store.list(project_id="p")
    assert len(items) == 2
    assert all(isinstance(wf, SavedWorkflow) for wf in items)


@pytest.mark.asyncio
async def test_list_with_is_active_filter(dialect_engine):
    """list(is_active=True/False) filters correctly."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="active-wf", yaml_content="c", project_id="p")
    await store.save(name="other-wf", yaml_content="c", project_id="p")
    await store.delete(wf_id, project_id="p")

    active = await store.list(is_active=True, project_id="p")
    inactive = await store.list(is_active=False, project_id="p")
    assert len(active) == 1
    assert active[0].name == "other-wf"
    assert len(inactive) == 1
    assert inactive[0].name == "active-wf"


@pytest.mark.asyncio
async def test_list_with_search(dialect_engine):
    """list(search=...) filters by name/description (case-insensitive)."""
    store = _make_store(dialect_engine)
    await store.save(
        name="research-pipeline",
        yaml_content="c1",
        description="Research tasks",
        project_id="p",
    )
    await store.save(
        name="data-pipeline",
        yaml_content="c2",
        description="Data processing",
        project_id="p",
    )
    # Search by name fragment
    items = await store.list(search="research", project_id="p")
    assert len(items) == 1
    assert items[0].name == "research-pipeline"


@pytest.mark.asyncio
async def test_list_pagination(dialect_engine):
    """list(limit=, offset=) pages correctly."""
    store = _make_store(dialect_engine)
    for i in range(5):
        await store.save(name=f"wf-{i}", yaml_content="c", project_id="p")
    page1 = await store.list(limit=3, offset=0, project_id="p")
    page2 = await store.list(limit=3, offset=3, project_id="p")
    assert len(page1) == 3
    assert len(page2) == 2


@pytest.mark.asyncio
async def test_delete_soft_deletes(dialect_engine):
    """delete() soft-deletes (is_active=False); get_by_name() filters it out."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="to-delete", yaml_content="c", project_id="p")
    result = await store.delete(wf_id, project_id="p")
    assert result is True

    # get_by_name filters inactive
    wf = await store.get_by_name("to-delete", project_id="p")
    assert wf is None

    # get() by id still returns it (but inactive)
    wf = await store.get(wf_id, project_id="p")
    assert wf is not None
    assert wf.is_active is False


@pytest.mark.asyncio
async def test_delete_missing_returns_false(dialect_engine):
    """delete() returns False for non-existent id."""
    store = _make_store(dialect_engine)
    result = await store.delete("nonexistent", project_id="p")
    assert result is False


@pytest.mark.asyncio
async def test_count_active_only(dialect_engine):
    """count() counts only active workflows."""
    store = _make_store(dialect_engine)
    assert await store.count(project_id="p") == 0
    id1 = await store.save(name="a", yaml_content="c", project_id="p")
    await store.save(name="b", yaml_content="c", project_id="p")
    assert await store.count(project_id="p") == 2
    await store.delete(id1, project_id="p")
    assert await store.count(project_id="p") == 1


@pytest.mark.asyncio
async def test_project_isolation(dialect_engine):
    """Workflows in different projects don't bleed across."""
    store = _make_store(dialect_engine)
    await store.save(name="wf", yaml_content="c", project_id="proj-a")
    await store.save(name="wf", yaml_content="c", project_id="proj-b")

    items_a = await store.list(project_id="proj-a")
    items_b = await store.list(project_id="proj-b")
    assert len(items_a) == 1
    assert len(items_b) == 1
    assert items_a[0].project_id == "proj-a"
    assert items_b[0].project_id == "proj-b"


@pytest.mark.asyncio
async def test_list_versions_project_scoped(dialect_engine):
    """list_versions() respects project scope."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="wf", yaml_content="c", project_id="proj-a")
    await store.save(name="wf", yaml_content="c2", project_id="proj-a")

    # Wrong project — should return []
    versions = await store.list_versions(wf_id, project_id="proj-b")
    assert versions == []

    versions = await store.list_versions(wf_id, project_id="proj-a")
    assert len(versions) == 2


@pytest.mark.asyncio
async def test_list_versions_shape(dialect_engine):
    """list_versions() returns dicts with required keys."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="wf", yaml_content="c1", project_id="p")

    versions = await store.list_versions(wf_id, project_id="p")
    assert len(versions) == 1
    v = versions[0]
    assert "id" in v
    assert "workflow_id" in v
    assert v["workflow_id"] == wf_id
    assert "version" in v
    assert "yaml_content" in v
    assert "created_at" in v
    assert v["version"] == 1
    assert v["yaml_content"] == "c1"


@pytest.mark.asyncio
async def test_created_by_stored(dialect_engine):
    """created_by field is persisted and returned."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(
        name="wf",
        yaml_content="c",
        created_by="admin@example.com",
        project_id="p",
    )
    wf = await store.get(wf_id, project_id="p")
    assert wf.created_by == "admin@example.com"


@pytest.mark.asyncio
async def test_to_dict_shape(dialect_engine):
    """SavedWorkflow.to_dict() returns all expected keys."""
    store = _make_store(dialect_engine)
    wf_id = await store.save(name="wf", yaml_content="c", project_id="p")
    wf = await store.get(wf_id, project_id="p")
    d = wf.to_dict()
    for key in ("id", "project_id", "name", "description", "yaml_content",
                "version", "is_active", "created_by", "created_at", "updated_at"):
        assert key in d, f"Missing key: {key}"
