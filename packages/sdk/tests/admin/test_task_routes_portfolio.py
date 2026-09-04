# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The Task portfolio route fans out over the caller's server-side project set."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.api_token_store import ApiTokenStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile
from sagewai.db.engine import create_engine
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import BoardColumn, TaskStatus
from sagewai.work.tasks.store import TaskStore
from tests.work.tasks.test_store import NOW, _record, _task


@pytest_asyncio.fixture
async def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    store = IdentityStore(engine=engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    pb = (await store.create_project(oid, "pb", "PB"))["id"]

    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member["id"], "project:member", project_id=pa)

    both = await store.create_user(oid, "both@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, both["id"], "project:member", project_id=pa)
    await store.add_membership(oid, both["id"], "project:member", project_id=pb)

    viewer = await store.create_user(oid, "v@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, viewer["id"], "project:viewer", project_id=pa)

    org_admin = await store.create_user(oid, "admin@acme.io", password="pw0000", role="org:admin")
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    res = AdminResourceStore(engine=engine)
    await res.init()
    token_store = ApiTokenStore(engine=engine)
    await token_store.init()
    task_store = TaskStore(engine=engine)
    await task_store.init()

    app = create_admin_serve_app(
        sf,
        identity_store=store,
        admin_resource_store=res,
        api_token_store=token_store,
    )
    app.state.task_store = task_store
    yield {
        "app": app,
        "identity_store": store,
        "api_token_store": token_store,
        "oid": oid,
        "pa": pa,
        "pb": pb,
        "member": await store.issue_session(oid, member["id"]),
        "both_user": both,
        "both": await store.issue_session(oid, both["id"]),
        "viewer": await store.issue_session(oid, viewer["id"]),
        "org_admin_user": org_admin,
        "org_admin": await store.issue_session(oid, org_admin["id"]),
    }
    await engine.dispose()


async def _seed_task(
    app,
    task_id: str,
    *,
    project_id: str,
    status: str = "PLANNING",
    board_column: str = "inbox",
) -> None:
    """One Task straight through the store, so the route has something to fan out over."""
    task = _task(task_id, project_id=project_id)
    record = _record(task).model_copy(
        update={"status": TaskStatus(status), "board_column": BoardColumn(board_column)}
    )
    events = (
        TaskEvent(
            id=f"{task_id}-1",
            project_id=project_id,
            task_id=task_id,
            sequence=1,
            event_type=TaskEventType.TASK_CREATED,
            actor_type="human",
            actor_ref="arda",
            payload_json={"title": task.title},
            created_at=NOW,
        ),
    )
    await app.state.task_store.create(
        task, events=events, record=record.model_copy(update={"last_event_sequence": 1})
    )


async def _get(app, path: str, *, token: str, project: str):
    headers = {"authorization": f"Bearer {token}", "x-project-id": project}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path, headers=headers)


async def _read_api_token(app_ctx, *, user_id: str, project_id: str) -> str:
    ctx = await app_ctx["identity_store"].build_context(
        app_ctx["oid"], user_id, project_id=project_id
    )
    _, plaintext = await app_ctx["api_token_store"].create_for(
        ctx, name="read", scopes={"read"}, project_id=project_id
    )
    return plaintext


@pytest.mark.asyncio
async def test_the_portfolio_spans_every_project_the_caller_belongs_to(app_ctx):
    """A member of both projects sees both, with the selected one first."""
    app, token, pa, pb = app_ctx["app"], app_ctx["both"], app_ctx["pa"], app_ctx["pb"]
    await _seed_task(app, "t-a", project_id=pa)
    await _seed_task(app, "t-b", project_id=pb, status="BLOCKED", board_column="needs_you")

    response = await _get(app, "/api/v1/tasks/portfolio", token=token, project=pa)

    assert response.status_code == 200
    projects = response.json()["projects"]
    assert [entry["project_id"] for entry in projects] == [pa, pb]
    assert [entry["needs_you"] for entry in projects] == [0, 1]
    assert [task["task_id"] for task in projects[1]["tasks"]] == ["t-b"]


@pytest.mark.asyncio
async def test_the_portfolio_hides_a_project_the_caller_does_not_belong_to(app_ctx):
    """The ``projects=`` parameter is ignored: a client-supplied list never widens the fan-out."""
    app, token, pa, pb = app_ctx["app"], app_ctx["member"], app_ctx["pa"], app_ctx["pb"]
    await _seed_task(app, "t-a", project_id=pa)
    await _seed_task(app, "t-b", project_id=pb)

    response = await _get(app, f"/api/v1/tasks/portfolio?projects={pb}", token=token, project=pa)

    assert response.status_code == 200
    assert [entry["project_id"] for entry in response.json()["projects"]] == [pa]


@pytest.mark.asyncio
async def test_a_project_bound_api_token_only_reads_its_project(app_ctx):
    app, pa, pb = app_ctx["app"], app_ctx["pa"], app_ctx["pb"]
    await _seed_task(app, "t-a", project_id=pa)
    await _seed_task(app, "t-b", project_id=pb)
    token = await _read_api_token(app_ctx, user_id=app_ctx["both_user"]["id"], project_id=pa)

    response = await _get(app, "/api/v1/tasks/portfolio", token=token, project=pa)

    assert response.status_code == 200
    assert [entry["project_id"] for entry in response.json()["projects"]] == [pa]


@pytest.mark.asyncio
async def test_an_org_admin_project_bound_api_token_only_reads_its_project(app_ctx):
    app, pa, pb = app_ctx["app"], app_ctx["pa"], app_ctx["pb"]
    await _seed_task(app, "t-a", project_id=pa)
    await _seed_task(app, "t-b", project_id=pb)
    token = await _read_api_token(app_ctx, user_id=app_ctx["org_admin_user"]["id"], project_id=pa)

    response = await _get(app, "/api/v1/tasks/portfolio", token=token, project=pa)

    assert response.status_code == 200
    assert [entry["project_id"] for entry in response.json()["projects"]] == [pa]


@pytest.mark.asyncio
async def test_an_org_admin_sees_every_project_in_the_org(app_ctx):
    """An org admin holds no per-project membership row, so the fan-out reads the org."""
    app, token, pa, pb = app_ctx["app"], app_ctx["org_admin"], app_ctx["pa"], app_ctx["pb"]
    await _seed_task(app, "t-a", project_id=pa)
    await _seed_task(app, "t-b", project_id=pb)

    response = await _get(app, "/api/v1/tasks/portfolio", token=token, project=pa)

    assert {entry["project_id"] for entry in response.json()["projects"]} == {pa, pb}


@pytest.mark.asyncio
async def test_the_global_scope_never_reaches_the_portfolio(app_ctx):
    """The middleware 404s ``global`` on a non-Work-read path before the handler runs.

    ``_project_hint`` passes ``"global"`` straight to ``build_context`` here
    (`auth_middleware.py:249-266`), which raises and is mapped to 404
    (`auth_middleware.py:425-427`). The handler's 400 is asserted where it is reachable — the
    per-router tests, which run without the middleware.
    """
    app, token = app_ctx["app"], app_ctx["member"]

    response = await _get(app, "/api/v1/tasks/portfolio", token=token, project="global")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_viewer_may_read_the_portfolio(app_ctx):
    app, token, pa = app_ctx["app"], app_ctx["viewer"], app_ctx["pa"]
    await _seed_task(app, "t-a", project_id=pa)

    response = await _get(app, "/api/v1/tasks/portfolio", token=token, project=pa)

    assert response.status_code == 200
    assert [entry["project_id"] for entry in response.json()["projects"]] == [pa]
