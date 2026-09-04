# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Every Task route hides real rows from other projects and sits behind the right tier."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.api_token_store import ApiTokenStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tasks_routes import (
    PROJECT_ADMIN_ROUTES,
    artifacts_router,
    work_router,
)
from sagewai.admin.tasks_routes import (
    router as task_router,
)
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.db.engine import create_engine
from sagewai.work import (
    ActionRequest,
    Reversibility,
    WorkActivityStore,
    WorkEvent,
    WorkEventType,
    WorkRecord,
    WorkStore,
)
from sagewai.work.tasks import TaskService, TaskStore
from sagewai.work.tasks.events import TaskEventType, fold_record
from sagewai.work.tasks.models import Budget
from tests.work.tasks.test_store import NOW, _event, _record, _task

_EXPECTED_ROUTE_COUNT = 29
_ROUTE_ENUMERATION_CONTRACT = "a new Task route must be enumerated by this isolation suite"
_EXPECTED_MEMBER_TASK_BOUND_ROUTE_COUNT = 13
_EXPECTED_TOKEN_READ_TASK_BOUND_ROUTE_COUNT = 8
_WORK_ACTIVITY_ROUTE = ("GET", "/api/v1/work/{work_id}/activity")
_WORK_WRITE_ROUTE = ("POST", "/api/v1/work/{work_id}/gates/{gate_id}")
_TASK_COLLECTION_ASSERTIONS = (
    ("GET", "/api/v1/tasks"),
    ("GET", "/api/v1/tasks/board"),
    ("GET", "/api/v1/tasks/decisions"),
    ("GET", "/api/v1/tasks/portfolio"),
)
_PROJECT_ADMIN_B_RESOURCE_ROUTES = (
    ("POST", "/api/v1/tasks/{task_id}/actions/{action_id}/rollback"),
    ("PATCH", "/api/v1/tasks/{task_id}"),
    _WORK_WRITE_ROUTE,
)
_NO_JSON = object()

BRIEF = (
    "Implement the retry queue in the payments service repository, add the failing test first, "
    "and open a pull request when the deterministic verification command passes."
)
PROJECT_B_BRIEF = "Project B owns this brief."


@dataclass(frozen=True)
class ProjectBSeed:
    task_id: str
    work_id: str
    storage_ref: str
    question_id: str
    attention_version: int
    gate_id: str
    work_gate_id: str
    action_id: str


def _defaults_body(project_id: str) -> dict:
    """Project defaults with a software target, so intake has something to route onto.

    ``TaskDefaults.target`` is a discriminated union (`models.py:272`), so the payload must
    carry ``kind`` or validation refuses it before the route is reached.
    """
    return {
        "project_id": project_id,
        "target": {
            "kind": "software",
            "repository_path": "/tmp/repo",
            "owner": "o",
            "repo": "r",
            "verification_image": "sha256:" + "b" * 64,
        },
    }


def _trigger_body(project_id: str) -> dict:
    return {
        "trigger": {
            "trigger_id": "tr-1",
            "project_id": project_id,
            "source": "github_label",
            "filter": {"owner": "o", "repo": "r", "label": "sagewai"},
            "template_id": "software_delivery",
            "template_version": "1",
        }
    }


def _budget_body() -> dict:
    return Budget().model_dump(mode="json")


def _question(question_id: str, attention_version: int) -> dict:
    return {
        "id": question_id,
        "text": "Which branch should be used?",
        "kind": "text",
        "options": [],
        "default": None,
        "defaultable": False,
        "rationale": "",
        "attention_version": attention_version,
    }


def _work_action(project_id: str, work_id: str) -> dict:
    return ActionRequest(
        project_id=project_id,
        action="merge",
        work_id=work_id,
        risk="medium",
        reversibility=Reversibility.COMPENSATABLE,
        scope="https://github.com/o/r/pull/3",
        evidence_refs=("pr://3",),
        rollback="revert_pull_request",
        post_check="merged_sha_read_back",
    ).model_dump(mode="json")


async def _seed_project_b(app, project_id: str) -> ProjectBSeed:
    brief = app.state.artifact_store.put_bytes(
        PROJECT_B_BRIEF.encode(),
        project_id=project_id,
        media_type="text/markdown",
        created_by="seed",
    )
    seed = ProjectBSeed(
        task_id="task-b",
        work_id="work-b",
        storage_ref=brief.storage_ref.removeprefix("artifact://"),
        question_id="question-b",
        attention_version=2,
        gate_id="plan:task-b:1",
        work_gate_id="merge:work-b:3",
        action_id="deliver:work-b:2",
    )
    task = _task(seed.task_id, project_id=project_id).model_copy(
        update={"brief_ref": brief, "brief_summary": PROJECT_B_BRIEF}
    )
    events = (
        _event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),
        _event(
            task,
            2,
            TaskEventType.BRIEF_RECORDED,
            {"brief_ref": brief.storage_ref, "summary": PROJECT_B_BRIEF},
        ),
        _event(
            task,
            3,
            TaskEventType.CLARIFICATION_REQUESTED,
            {
                "questions": [_question(seed.question_id, seed.attention_version)],
                "deadline_at": NOW.isoformat(),
            },
        ),
        _event(task, 4, TaskEventType.CYCLE_STARTED, {"cycle": 1, "scheduled_for": None}),
        _event(
            task,
            5,
            TaskEventType.STEP_WORK_STARTED,
            {
                "step_id": "s1",
                "work_id": seed.work_id,
                "issue_url": "https://github.com/o/r/issues/1",
                "base_sha": "a" * 40,
            },
        ),
        _event(
            task,
            6,
            TaskEventType.ACTION_INTENT_RECORDED,
            {
                "action_id": seed.action_id,
                "work_id": seed.work_id,
                "gate_id": seed.action_id,
                "action": _work_action(project_id, seed.work_id),
            },
        ),
        _event(
            task,
            7,
            TaskEventType.GATE_REQUESTED,
            {"gate_id": seed.gate_id, "question": "Approve the plan."},
        ),
    )
    await app.state.task_store.create(
        task, events=events, record=fold_record(_record(task), events)
    )
    await _seed_work_gate(app.state.work_store, seed.work_id, project_id=project_id)
    return seed


async def _seed_work_gate(store: WorkStore, work_id: str, *, project_id: str) -> None:
    await store.save_work(
        WorkRecord(
            work_id=work_id,
            project_id=project_id,
            source_ref="https://github.com/o/r/issues/1",
            profile="software",
            status="READY_TO_MERGE",
            active_run_id=None,
            pending_gate=f"merge:{work_id}:3",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    for sequence, (event_type, payload) in enumerate(
        (
            (WorkEventType.WORK_CREATED, {"work_id": work_id}),
            (
                WorkEventType.GATE_REQUESTED,
                {
                    "gate_id": f"merge:{work_id}:3",
                    "question": "Approve merge of PR #3.",
                    "action": _work_action(project_id, work_id),
                },
            ),
        ),
        start=1,
    ):
        await store.append_event(
            WorkEvent(
                id=f"{work_id}-{sequence}",
                project_id=project_id,
                work_id=work_id,
                sequence=sequence,
                event_type=event_type,
                actor_type="github_lifecycle",
                actor_ref="policy",
                payload_json=payload,
                created_at=NOW,
            )
        )


@pytest_asyncio.fixture
async def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    identity = IdentityStore(engine=engine)
    await identity.init()
    org_id = (await identity.bootstrap_org("Acme", "acme"))["id"]
    project_a = (await identity.create_project(org_id, "pa", "PA"))["id"]
    project_b = (await identity.create_project(org_id, "pb", "PB"))["id"]

    sessions = {}
    for label, role in (
        ("viewer", "project:viewer"),
        ("member", "project:member"),
        ("project_admin", "project:admin"),
    ):
        user = await identity.create_user(
            org_id, f"{label}@acme.io", password="pw0000", role="org:member"
        )
        await identity.add_membership(org_id, user["id"], role, project_id=project_a)
        sessions[label] = await identity.issue_session(org_id, user["id"])
    both = await identity.create_user(org_id, "both@acme.io", password="pw0000", role="org:member")
    await identity.add_membership(org_id, both["id"], "project:member", project_id=project_a)
    await identity.add_membership(org_id, both["id"], "project:member", project_id=project_b)

    state = AdminStateFile(path=tmp_path / "state.json")
    state.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    resources = AdminResourceStore(engine=engine)
    await resources.init()
    token_store = ApiTokenStore(engine=engine)
    await token_store.init()

    app = create_admin_serve_app(
        state,
        identity_store=identity,
        admin_resource_store=resources,
        api_token_store=token_store,
    )
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit

    task_store = TaskStore(engine=engine)
    await task_store.init()
    work_store = WorkStore(engine=engine)
    await work_store.init()
    activity_store = WorkActivityStore(engine=engine)
    await activity_store.init()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    app.state.task_store = task_store
    app.state.work_store = work_store
    app.state.activity_store = activity_store
    app.state.artifact_store = artifacts
    app.state.task_service = TaskService(store=task_store, artifact_store=artifacts)
    seed_b = await _seed_project_b(app, project_b)

    yield {
        "app": app,
        "identity_store": identity,
        "api_token_store": token_store,
        "org_id": org_id,
        "pa": project_a,
        "pb": project_b,
        "both_user": both,
        "seed_b": seed_b,
        **sessions,
    }
    await engine.dispose()


def _declared_task_routes() -> set[tuple[str, str]]:
    out = set()
    for api_router in (task_router, work_router, artifacts_router):
        for route in api_router.routes:
            for method in getattr(route, "methods", ()) or ():
                if method != "HEAD":
                    out.add((method, route.path))
    assert len(out) == _EXPECTED_ROUTE_COUNT, _ROUTE_ENUMERATION_CONTRACT
    return out


def _task_routes(app) -> list[tuple[str, str]]:
    """Every route declared across the Task, Work-task, and Artifact routers."""
    declared = _declared_task_routes()
    out = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods:
            if method == "HEAD":
                continue
            if (method, path) in declared:
                out.append((method, path))
    assert set(out) == declared, _ROUTE_ENUMERATION_CONTRACT
    assert len(out) == _EXPECTED_ROUTE_COUNT, _ROUTE_ENUMERATION_CONTRACT
    return sorted(out)


def _url_for_route(path: str, seed: ProjectBSeed | None) -> str:
    if seed is None:
        return re.sub(r"\{[^}]+\}", "x", path)
    gate_id = seed.work_gate_id if path.startswith("/api/v1/work/") else seed.gate_id
    return (
        path.replace("{task_id}", seed.task_id)
        .replace("{work_id}", seed.work_id)
        .replace("{storage_ref}", seed.storage_ref)
        .replace("{gate_id}", gate_id)
        .replace("{action_id}", seed.action_id)
        .replace("{trigger_id}", "x")
    )


def _params_for_route(path: str, seed: ProjectBSeed | None) -> dict[str, str] | None:
    if path != "/api/v1/artifacts/{storage_ref}":
        return None
    return {"task_id": seed.task_id if seed is not None else "x"}


def _body_for_route(
    method: str, path: str, *, project_id: str, seed: ProjectBSeed | None
) -> object:
    if method in {"GET", "DELETE"}:
        return _NO_JSON
    if (method, path) == ("POST", "/api/v1/tasks"):
        return {"brief": BRIEF}
    if (method, path) == ("POST", "/api/v1/tasks/intake"):
        return {"brief": BRIEF}
    if (method, path) == ("PUT", "/api/v1/tasks/defaults"):
        return {"defaults": _defaults_body(project_id), "expected_revision": 0}
    if (method, path) == ("POST", "/api/v1/tasks/triggers"):
        return _trigger_body(project_id)
    if path.endswith("/messages"):
        return {"text": "hi"}
    if path.endswith("/answers"):
        return {
            "attention_id": seed.question_id if seed is not None else "x",
            "attention_version": seed.attention_version if seed is not None else 1,
            "answer": "x",
        }
    if "/gates/" in path:
        return {"decision": "allow"}
    if path.endswith("/cancel"):
        return {}
    if method == "PATCH":
        return {"budget": _budget_body(), "revision": 1}
    return _NO_JSON


async def _hit(
    app,
    method: str,
    path: str,
    *,
    token: str,
    project: str,
    seed: ProjectBSeed | None = None,
):
    url = _url_for_route(path, seed)
    params = _params_for_route(path, seed)
    body = _body_for_route(method, path, project_id=project, seed=seed)
    headers = {"authorization": f"Bearer {token}", "x-project-id": project}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        kwargs = {"headers": headers, "params": params}
        if body is not _NO_JSON:
            kwargs["json"] = body
        return await client.request(method, url, **kwargs)


async def _read_api_token(app_ctx, *, user_id: str, project_id: str) -> str:
    ctx = await app_ctx["identity_store"].build_context(
        app_ctx["org_id"], user_id, project_id=project_id
    )
    _, plaintext = await app_ctx["api_token_store"].create_for(
        ctx, name="read", scopes={"read"}, project_id=project_id
    )
    return plaintext


def _task_bound_routes(routes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        route
        for route in routes
        if "{task_id}" in route[1]
        or route[1] == "/api/v1/artifacts/{storage_ref}"
        or route == _WORK_ACTIVITY_ROUTE
    ]


def _member_task_bound_routes(routes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        route
        for route in _task_bound_routes(routes)
        if route not in PROJECT_ADMIN_ROUTES and route != _WORK_ACTIVITY_ROUTE
    ]


async def _assert_collections_hide_project_b(
    app, *, token: str, project_id: str, seed: ProjectBSeed
) -> None:
    tasks = await _hit(app, "GET", "/api/v1/tasks", token=token, project=project_id)
    board = await _hit(app, "GET", "/api/v1/tasks/board", token=token, project=project_id)
    decisions = await _hit(app, "GET", "/api/v1/tasks/decisions", token=token, project=project_id)
    portfolio = await _hit(app, "GET", "/api/v1/tasks/portfolio", token=token, project=project_id)

    assert tasks.status_code == 200
    assert board.status_code == 200
    assert decisions.status_code == 200
    assert portfolio.status_code == 200
    assert seed.task_id not in {task["task_id"] for task in tasks.json()["tasks"]}
    assert seed.task_id not in {
        task["task_id"] for column in board.json()["columns"].values() for task in column
    }
    assert seed.task_id not in {item.get("task_id") for item in decisions.json()["items"]}
    assert seed.work_id not in {item.get("work_id") for item in decisions.json()["items"]}
    assert all(
        seed.task_id not in {task["task_id"] for task in project["tasks"]}
        for project in portfolio.json()["projects"]
    )


@pytest.mark.asyncio
async def test_every_task_route_hides_a_foreign_project(app_ctx):
    """A member of project A naming project B is 404'd by the middleware, never served."""
    app, token, pb = app_ctx["app"], app_ctx["member"], app_ctx["pb"]
    routes = _task_routes(app)
    wrong = []
    for method, path in routes:
        response = await _hit(app, method, path, token=token, project=pb)
        if response.status_code != 404:
            wrong.append(f"{method} {path} -> {response.status_code}")
    assert not wrong, "a foreign project header was not hidden:\n" + "\n".join(wrong)


@pytest.mark.asyncio
async def test_the_global_scope_never_reaches_a_task_route(app_ctx):
    """In multi-tenant mode the middleware answers 404 before any Task handler runs.

    ``_project_hint`` strips ``global`` only for the Work read paths
    (`auth_middleware.py:249-266`); for everything else it hands ``"global"`` to
    ``build_context``, which raises ``TenantAccessError("unknown project")``
    (`identity_store.py:692`) and is mapped to 404 (`auth_middleware.py:425-427`). So the
    handler's own 400 is unreachable here, and this asserts the outcome that matters: the
    global scope never reaches a Task. The 400 itself is proved by the per-router tests, which
    run without the middleware, and is what a single-org deployment answers.
    """
    app, token = app_ctx["app"], app_ctx["member"]
    routes = _task_routes(app)
    wrong = []
    checked = 0
    for method, path in routes:
        if (method, path) == _WORK_ACTIVITY_ROUTE:
            continue
        checked += 1
        response = await _hit(app, method, path, token=token, project="global")
        if response.status_code != 404:
            wrong.append(f"{method} {path} -> {response.status_code}")
    assert checked == _EXPECTED_ROUTE_COUNT - 1
    assert not wrong, "the global scope was not refused:\n" + "\n".join(wrong)


@pytest.mark.asyncio
async def test_project_a_member_cannot_reach_project_b_real_task_ids(app_ctx):
    app = app_ctx["app"]
    routes = _task_routes(app)
    task_bound_routes = _member_task_bound_routes(routes)
    assert len(task_bound_routes) == _EXPECTED_MEMBER_TASK_BOUND_ROUTE_COUNT
    wrong = []
    for method, path in task_bound_routes:
        try:
            response = await asyncio.wait_for(
                _hit(
                    app,
                    method,
                    path,
                    token=app_ctx["member"],
                    project=app_ctx["pa"],
                    seed=app_ctx["seed_b"],
                ),
                timeout=4,
            )
        except asyncio.TimeoutError:
            wrong.append(f"{method} {path} -> streamed instead of 404")
            continue
        if response.status_code != 404:
            wrong.append(f"{method} {path} -> {response.status_code}")
    assert not wrong, "a project B Task row leaked under project A:\n" + "\n".join(wrong)


@pytest.mark.asyncio
async def test_project_a_collections_hide_project_b_rows(app_ctx):
    app = app_ctx["app"]
    routes = _task_routes(app)
    assert all(route in routes for route in _TASK_COLLECTION_ASSERTIONS)
    await _assert_collections_hide_project_b(
        app, token=app_ctx["member"], project_id=app_ctx["pa"], seed=app_ctx["seed_b"]
    )
    work_activity = await _hit(
        app,
        "GET",
        "/api/v1/work/{work_id}/activity",
        token=app_ctx["member"],
        project=app_ctx["pa"],
        seed=app_ctx["seed_b"],
    )
    work_gate = await _hit(
        app,
        "POST",
        "/api/v1/work/{work_id}/gates/{gate_id}",
        token=app_ctx["project_admin"],
        project=app_ctx["pa"],
        seed=app_ctx["seed_b"],
    )
    assert work_activity.status_code == 404
    assert work_gate.status_code == 404


@pytest.mark.asyncio
async def test_project_a_project_admin_cannot_reach_project_b_admin_routes(app_ctx):
    app = app_ctx["app"]
    routes = _task_routes(app)
    assert all(route in routes for route in PROJECT_ADMIN_ROUTES)
    wrong = []
    for method, path in _PROJECT_ADMIN_B_RESOURCE_ROUTES:
        response = await _hit(
            app,
            method,
            path,
            token=app_ctx["project_admin"],
            project=app_ctx["pa"],
            seed=app_ctx["seed_b"],
        )
        if response.status_code != 404:
            wrong.append(f"{method} {path} -> {response.status_code}")
    assert not wrong, "a project admin reached another project's row:\n" + "\n".join(wrong)


@pytest.mark.asyncio
async def test_project_bound_read_token_cannot_reach_project_b_real_read_ids(app_ctx):
    app, pa = app_ctx["app"], app_ctx["pa"]
    routes = _task_routes(app)
    token = await _read_api_token(app_ctx, user_id=app_ctx["both_user"]["id"], project_id=pa)
    read_routes = [route for route in _task_bound_routes(routes) if route[0] == "GET"]
    assert len(read_routes) == _EXPECTED_TOKEN_READ_TASK_BOUND_ROUTE_COUNT
    wrong = []
    for method, path in read_routes:
        try:
            response = await asyncio.wait_for(
                _hit(app, method, path, token=token, project=pa, seed=app_ctx["seed_b"]),
                timeout=4,
            )
        except asyncio.TimeoutError:
            wrong.append(f"{method} {path} -> streamed instead of 404")
            continue
        if response.status_code != 404:
            wrong.append(f"{method} {path} -> {response.status_code}")
    assert not wrong, "a project-bound read token reached project B:\n" + "\n".join(wrong)
    portfolio = await _hit(app, "GET", "/api/v1/tasks/portfolio", token=token, project=pa)
    assert portfolio.status_code == 200
    assert [entry["project_id"] for entry in portfolio.json()["projects"]] == [pa]


@pytest.mark.asyncio
async def test_a_viewer_reads_and_never_writes(app_ctx):
    app, token, pa = app_ctx["app"], app_ctx["viewer"], app_ctx["pa"]

    listed = await _hit(app, "GET", "/api/v1/tasks", token=token, project=pa)
    created = await _hit(app, "POST", "/api/v1/tasks", token=token, project=pa)

    assert listed.status_code == 200
    assert listed.json()["tasks"] == []
    assert created.status_code == 403


@pytest.mark.asyncio
async def test_a_member_creates_reads_and_opens_its_own_thread(app_ctx):
    """The success path, so the isolation tests above are not passing on a broken app."""
    app, token, pa = app_ctx["app"], app_ctx["member"], app_ctx["pa"]
    headers = {"authorization": f"Bearer {token}", "x-project-id": pa}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        await client.put(
            "/api/v1/tasks/defaults",
            headers={"authorization": f"Bearer {app_ctx['project_admin']}", "x-project-id": pa},
            json={"defaults": _defaults_body(pa), "expected_revision": 0},
        )
        created = await client.post("/api/v1/tasks", headers=headers, json={"brief": BRIEF})
        task_id = created.json()["task"]["id"]
        listed = await client.get("/api/v1/tasks", headers=headers)
        thread = await client.get(f"/api/v1/tasks/{task_id}/thread", headers=headers)

    assert created.status_code == 201
    assert [task["task_id"] for task in listed.json()["tasks"]] == [task_id]
    assert thread.status_code == 200


@pytest.mark.asyncio
async def test_a_member_may_decide_a_plan_gate_but_not_a_deliver_gate(app_ctx):
    app, token, pa = app_ctx["app"], app_ctx["member"], app_ctx["pa"]
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        headers = {"authorization": f"Bearer {token}", "x-project-id": pa}
        plan = await client.post(
            "/api/v1/tasks/x/gates/plan:x:1", headers=headers, json={"decision": "allow"}
        )
        deliver = await client.post(
            "/api/v1/tasks/x/gates/deliver:w1:1", headers=headers, json={"decision": "allow"}
        )

    assert plan.status_code == 404
    assert deliver.status_code == 403
