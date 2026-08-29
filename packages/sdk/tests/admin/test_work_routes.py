# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Canonical, project-scoped Work Control Console read routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial

import pytest
from click.testing import CliRunner
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile
from sagewai.cli import cli
from sagewai.db import factory
from sagewai.work import WorkEvent, WorkEventType, WorkRecord

NOW = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SAGEWAI_DATABASE_URL", raising=False)
    factory.reset_engine()
    yield
    factory.reset_engine()


def _record(
    work_id: str,
    *,
    project_id: str,
    status: str,
    created_at: datetime = NOW,
) -> WorkRecord:
    return WorkRecord(
        work_id=work_id,
        project_id=project_id,
        source_ref=f"issue:{work_id}",
        profile="software",
        status=status,
        active_run_id=None,
        pending_gate=None,
        created_at=created_at,
        updated_at=created_at,
    )


def _event(
    event_id: str,
    *,
    work_id: str,
    project_id: str,
    sequence: int,
    event_type: WorkEventType,
    payload_json: dict,
    created_at: datetime = NOW,
) -> WorkEvent:
    return WorkEvent(
        id=event_id,
        project_id=project_id,
        work_id=work_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="operator",
        actor_ref="codex",
        payload_json=payload_json,
        created_at=created_at,
    )


def _app_and_token(tmp_path):
    state = AdminStateFile(path=tmp_path / "state.json")
    state.complete_setup(
        org_name="Acme",
        admin_email="admin@example.com",
        admin_password="pw123456",
    )
    app = create_admin_serve_app(state)
    token = state.validate_login("admin@example.com", "pw123456")["access_token"]
    return app, token


def test_active_work_list_is_exact_and_project_scoped(tmp_path) -> None:
    app, token = _app_and_token(tmp_path)
    active = _record("work-active", project_id="project-a", status="IMPLEMENTING")
    complete = _record(
        "work-complete",
        project_id="project-a",
        status="COMPLETE",
        created_at=NOW + timedelta(minutes=1),
    )
    foreign = _record("work-foreign", project_id="project-b", status="IMPLEMENTING")

    with TestClient(app) as client:
        client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-Project-ID": "project-a",
            }
        )
        client.portal.call(app.state.work_store.save_work, active)
        client.portal.call(app.state.work_store.save_work, complete)
        client.portal.call(app.state.work_store.save_work, foreign)

        response = client.get("/api/v1/work")

    assert response.status_code == 200
    assert response.json() == [jsonable_encoder(active)]


def test_work_detail_has_ordered_events_and_hides_foreign_work(tmp_path) -> None:
    app, token = _app_and_token(tmp_path)
    record = _record("work-a", project_id="project-a", status="IMPLEMENTING")
    foreign = _record("work-b", project_id="project-b", status="IMPLEMENTING")
    first = _event(
        "event-1",
        work_id=record.work_id,
        project_id=record.project_id,
        sequence=1,
        event_type=WorkEventType.WORK_CREATED,
        payload_json={"title": "First"},
    )
    second = _event(
        "event-2",
        work_id=record.work_id,
        project_id=record.project_id,
        sequence=2,
        event_type=WorkEventType.STAGE_STARTED,
        payload_json={"stage": "implementation"},
        created_at=NOW + timedelta(seconds=1),
    )

    with TestClient(app) as client:
        client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-Project-ID": "project-a",
            }
        )
        client.portal.call(app.state.work_store.save_work, record)
        client.portal.call(app.state.work_store.save_work, foreign)
        client.portal.call(app.state.work_store.append_event, second)
        client.portal.call(app.state.work_store.append_event, first)

        response = client.get(f"/api/v1/work/{record.work_id}")
        foreign_response = client.get(f"/api/v1/work/{foreign.work_id}")
        missing_response = client.get("/api/v1/work/missing")

    assert response.status_code == 200
    assert response.json() == {
        "work": jsonable_encoder(record),
        "events": [jsonable_encoder(first), jsonable_encoder(second)],
    }
    assert foreign_response.status_code == 404
    assert missing_response.status_code == 404


def test_pending_attention_matches_canonical_project_query(tmp_path) -> None:
    app, token = _app_and_token(tmp_path)
    blocked = _record("work-blocked", project_id="project-a", status="WORK_BLOCKED")
    foreign = _record("work-foreign", project_id="project-b", status="WORK_BLOCKED")
    blocked_event = _event(
        "blocked-a",
        work_id=blocked.work_id,
        project_id=blocked.project_id,
        sequence=1,
        event_type=WorkEventType.WORK_BLOCKED,
        payload_json={
            "reason": "Approval required",
            "decision_request": "Approve production rollout?",
            "evidence_refs": ["artifact:preflight"],
        },
    )
    foreign_event = _event(
        "blocked-b",
        work_id=foreign.work_id,
        project_id=foreign.project_id,
        sequence=1,
        event_type=WorkEventType.WORK_BLOCKED,
        payload_json={"reason": "Foreign blocker"},
    )

    with TestClient(app) as client:
        client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-Project-ID": "project-a",
            }
        )
        client.portal.call(app.state.work_store.save_work, blocked)
        client.portal.call(app.state.work_store.save_work, foreign)
        client.portal.call(app.state.work_store.append_event, blocked_event)
        client.portal.call(app.state.work_store.append_event, foreign_event)
        expected = client.portal.call(
            partial(
                app.state.work_store.pending_attention,
                project_id="project-a",
            )
        )

        response = client.get("/api/v1/work/pending")
        cli_response = CliRunner().invoke(
            cli,
            ["work", "--project", "project-a", "pending"],
        )

    assert response.status_code == 200
    assert response.json() == jsonable_encoder(expected)
    assert [item["work_id"] for item in response.json()] == [blocked.work_id]
    assert cli_response.exit_code == 0, cli_response.output
    assert cli_response.output.splitlines() == [
        (
            f"{item['kind']} {item['work_id']} {item['attention_id']}: "
            f"{item['summary']}"
        )
        for item in response.json()
    ]
    assert foreign.work_id not in cli_response.output
