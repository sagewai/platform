# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from sagewai.admin.local_training import JobConflictError, TrainingJobs, TrainingRequest
from sagewai.admin.state_file import AdminStateFile


def payload():
    return TrainingRequest(
        dataset_id="synthetic-dataset",
        manifest_hash="a" * 64,
        base_model="synthetic:base",
        samples=[{"instruction": "Question", "output": "Answer"}],
    )


def test_jobs_are_durable_idempotent_and_exactly_project_scoped(tmp_path):
    jobs = TrainingJobs(tmp_path)
    ident = str(uuid4())
    first = jobs.submit("org", "one", ident, payload())
    assert jobs.submit("org", "one", ident, payload()) == first
    changed = payload().model_copy(update={"manifest_hash": "b" * 64})
    with pytest.raises(JobConflictError):
        jobs.submit("org", "one", ident, changed)
    assert jobs.get("org", "two", ident) is None
    assert jobs.get("another-org", "one", ident) is None
    other = jobs.submit("org", "two", ident, changed)
    assert other["project_id"] == "two"
    reopened = TrainingJobs(tmp_path)
    assert reopened.get("org", "one", ident) == first
    assert reopened.claim()["id"] == ident
    assert reopened.claim() is None
    reopened.recover()
    assert reopened.get("org", "one", ident)["status"] == "failed"
    assert reopened.get("org", "two", ident)["status"] == "queued"
    assert reopened.submit("org", "one", ident, payload())["status"] == "failed"


def test_cancelled_job_cannot_be_completed_or_read_outside_scope(tmp_path):
    jobs = TrainingJobs(tmp_path)
    ident = str(uuid4())
    jobs.submit("org", "one", ident, payload())
    job = jobs.claim()
    assert jobs.cancel("org", "two", ident)["status"] == "cancelled"
    assert jobs.cancel("org", "one", ident)["status"] == "cancelled"
    jobs.finish(
        job,
        {
            "candidate_model": "synthetic-candidate",
            "endpoint_url": "http://127.0.0.1:11434",
            "digest": "sha256:" + "c" * 64,
        },
    )
    assert jobs.get("org", "one", ident)["status"] == "cancelled"
    assert jobs.get("org", "one", ident)["candidate_model"] is None


def test_cancellation_tombstone_blocks_late_post_and_binds_immutable_lineage(tmp_path):
    jobs = TrainingJobs(tmp_path)
    ident = str(uuid4())
    tombstone = jobs.cancel("org", "one", ident)
    assert tombstone["id"] == ident and tombstone["status"] == "cancelled"
    assert "dataset_id" not in tombstone
    late = jobs.submit("org", "one", ident, payload())
    assert late["status"] == "cancelled" and late["dataset_id"] == "synthetic-dataset"
    assert jobs.submit("org", "one", ident, payload()) == late
    assert jobs.claim() is None
    with pytest.raises(JobConflictError):
        jobs.submit("org", "one", ident, payload().model_copy(update={"base_model": "changed"}))
    with jobs.connection() as db:
        assert db.execute("SELECT samples FROM jobs").fetchone()[0] is None


@pytest.mark.parametrize("terminal", ["completed", "failed", "recovered"])
def test_terminal_jobs_scrub_private_inputs_but_keep_weights_and_retry_identity(tmp_path, terminal):
    jobs = TrainingJobs(tmp_path)
    ident = str(uuid4())
    request = TrainingRequest.model_validate(
        payload().model_dump()
        | {"samples": [{"instruction": "private sentinel", "output": "private output"}]}
    )
    jobs.submit("org", "one", ident, request)
    job = jobs.claim()
    directory = jobs.directory("org", "one", ident)
    snapshot = directory / "model" / ident / "dataset.jsonl"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("private sentinel")
    (directory / "request.json").write_text("private sentinel")
    weights = snapshot.parent / "model.gguf"
    weights.write_bytes(b"GGUFsynthetic")
    if terminal == "recovered":
        jobs.recover()
    else:
        jobs.finish(
            job,
            {"candidate_model": "sagewai-trained-synthetic:latest", "context_window": 4096},
            reason="failed" if terminal == "failed" else None,
        )
    assert not snapshot.exists() and not (directory / "request.json").exists()
    assert weights.read_bytes() == b"GGUFsynthetic"
    assert b"private sentinel" not in jobs.path.read_bytes()
    assert b"private output" not in jobs.path.read_bytes()
    assert jobs.submit("org", "one", ident, request)["status"] != "queued"
    with pytest.raises(JobConflictError):
        jobs.submit("org", "one", ident, payload())


def test_training_api_preserves_setup_auth_and_explicit_project_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from sagewai.admin.serve import create_admin_serve_app

    sf = AdminStateFile(path=tmp_path / "state.json")
    jobs = TrainingJobs(tmp_path / "jobs")
    app = create_admin_serve_app(sf, training_jobs=jobs)
    client = TestClient(app)
    path = "/api/v1/training/jobs/" + str(uuid4())
    assert client.post(path, json=payload().model_dump()).status_code in (401, 403, 503)
    sf.complete_setup(
        org_name="Synthetic",
        admin_email="synthetic@example.test",
        admin_password="synthetic password",
    )
    sf.create_project(name="One", slug="one")
    sf.create_project(name="Two", slug="two")
    token = sf.create_api_token(name="Synthetic", scopes=["read", "write"])["token"]
    headers = {"Authorization": "Bearer " + token, "X-Project-ID": "one"}
    assert client.post(path, json=payload().model_dump()).status_code == 401
    assert (
        client.post(
            path, headers={"Authorization": "Bearer " + token}, json=payload().model_dump()
        ).status_code
        == 400
    )
    assert client.post(path, headers=headers, json=payload().model_dump()).status_code == 200
    assert (
        client.post(path, headers=headers, json=payload().model_dump()).json()["status"] == "queued"
    )
    changed = payload().model_dump() | {"dataset_id": "changed"}
    assert client.post(path, headers=headers, json=changed).status_code == 409
    job = jobs.claim()
    jobs.finish(
        job,
        {
            "candidate_model": "sagewai-trained-synthetic:latest",
            "endpoint_url": "http://127.0.0.1:11434",
            "digest": "sha256:" + "c" * 64,
            "context_window": 4096,
        },
    )
    receipt = client.get(path, headers=headers).json()
    assert receipt["status"] == "completed" and receipt["context_window"] == 4096
    assert {key: receipt[key] for key in ("dataset_id", "manifest_hash", "base_model")} == {
        key: payload().model_dump()[key] for key in ("dataset_id", "manifest_hash", "base_model")
    }
    foreign = headers | {"X-Project-ID": "two"}
    assert client.get(path, headers=foreign).status_code == 404
    assert client.delete(path, headers=foreign).json()["status"] == "cancelled"
    assert client.delete(path, headers=headers).json()["status"] == "cancelled"
    assert client.get(path, headers=headers).json()["manifest_hash"] == "a" * 64
    read = sf.create_api_token(name="Read", scopes=["read"])["token"]
    assert (
        client.post(
            path, headers=headers | {"Authorization": "Bearer " + read}, json=payload().model_dump()
        ).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_delete_fences_post_that_was_in_flight_before_cancellation(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    from sagewai.admin.serve import create_admin_serve_app

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(
        org_name="Synthetic",
        admin_email="synthetic@example.test",
        admin_password="synthetic password",
    )
    sf.create_project(name="One", slug="one")
    token = sf.create_api_token(name="Synthetic", scopes=["read", "write"])["token"]
    jobs = TrainingJobs(tmp_path / "jobs")
    app = create_admin_serve_app(sf, training_jobs=jobs)
    path = "/api/v1/training/jobs/" + str(uuid4())
    headers = {"Authorization": "Bearer " + token, "X-Project-ID": "one"}
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_body():
        started.set()
        await release.wait()
        yield payload().model_dump_json().encode()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        pending = asyncio.create_task(client.post(path, headers=headers, content=delayed_body()))
        await asyncio.wait_for(started.wait(), 2)
        cancelled = await client.delete(path, headers=headers)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        release.set()
        late = await asyncio.wait_for(pending, 2)
        assert late.status_code == 200 and late.json()["status"] == "cancelled"
        assert late.json()["dataset_id"] == "synthetic-dataset"
        assert jobs.claim() is None


@pytest.mark.asyncio
async def test_worker_serializes_jobs_and_stops_exact_cancelled_execution(tmp_path):
    from sagewai.admin.local_training import TrainingWorker

    jobs = TrainingJobs(tmp_path)
    ids = [str(uuid4()), str(uuid4())]
    for ident in ids:
        jobs.submit("org", "one", ident, payload())
    started = asyncio.Event()
    calls = []

    async def execute(job):
        calls.append(job["id"])
        started.set()
        await asyncio.Event().wait()

    worker = TrainingWorker(jobs, execute=execute)
    await worker.start()
    await asyncio.wait_for(started.wait(), 2)
    assert calls == ids[:1]
    await worker.cancel("org", "one", ids[0])
    await worker.close()
    assert jobs.get("org", "one", ids[0])["status"] == "cancelled"
    assert jobs.get("org", "one", ids[1])["status"] in ("queued", "failed")


def test_executor_missing_backend_or_export_never_reports_completion(tmp_path, monkeypatch):
    from sagewai.admin.local_training_executor import execute_local
    from sagewai.autopilot.curator.fine_tune import FineTuneExecutor, FineTuneResult

    model = tmp_path / "installed-model"
    model.mkdir()
    monkeypatch.setattr(
        "sagewai.admin.local_training_executor.ollama_models",
        lambda url: {"synthetic:base": "sha256:" + "b" * 64},
    )
    for result in [
        FineTuneResult(status="skipped", reason="No local backend"),
        FineTuneResult(status="completed", model_path=str(tmp_path), metrics={"gguf_path": None}),
    ]:
        monkeypatch.setattr(FineTuneExecutor, "execute", lambda *args: result)
        with pytest.raises(RuntimeError):
            execute_local(
                {"id": str(uuid4()), "project_id": "one", **payload().model_dump()},
                model_path=model,
                output_dir=tmp_path / "output",
                ollama_url="http://127.0.0.1:11434",
                candidate="synthetic-candidate:latest",
            )


@pytest.mark.asyncio
async def test_cancellation_terminates_exact_child_and_keeps_offline_environment(
    tmp_path, monkeypatch
):
    from sagewai.admin.local_training import TrainingWorker

    model = tmp_path / "installed"
    model.mkdir()
    monkeypatch.setenv("SAGEWAI_LOCAL_TRAINING_MODELS", json.dumps({"synthetic:base": str(model)}))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-admin-secret")
    jobs = TrainingJobs(tmp_path / "jobs")
    ident = str(uuid4())
    jobs.submit("org", "one", ident, payload())
    real_spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    processes = []
    directories = []

    async def synthetic_child(*args, **kwargs):
        assert args[1:3] == ("-m", "sagewai.admin.local_training_executor")
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert kwargs["env"]["TRANSFORMERS_OFFLINE"] == "1"
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["env"]["HOME"].startswith(str(jobs.root))
        recorded = json.loads(Path(args[3]).read_text())
        assert recorded["candidate"].startswith("sagewai-trained-")
        assert recorded["job"]["project_id"] == "one"
        directory = Path(recorded["output_dir"])
        snapshot = directory / "model" / ident / "dataset.jsonl"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text("synthetic private sample")
        directories.append(directory)
        process = await real_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", synthetic_child)
    worker = TrainingWorker(jobs)
    await worker.start()
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert (await worker.cancel("org", "foreign", ident))["status"] == "cancelled"
        assert processes[0].returncode is None
        assert (await worker.cancel("org", "one", ident))["status"] == "cancelled"
        # Shutdown immediately after DELETE must not interrupt child cleanup
        # with a second cancellation.
        await asyncio.wait_for(worker.close(), 2)
    finally:
        await worker.close()
    assert processes[0].returncode is not None
    assert jobs.get("org", "one", ident)["status"] == "cancelled"
    assert not (directories[0] / "request.json").exists()
    assert not (directories[0] / "model" / ident / "dataset.jsonl").exists()


@pytest.mark.asyncio
async def test_second_worker_cannot_recover_another_live_workers_job(tmp_path):
    from sagewai.admin.local_training import TrainingWorker

    jobs = TrainingJobs(tmp_path)
    ident, queued = str(uuid4()), str(uuid4())
    jobs.submit("org", "one", ident, payload())
    jobs.submit("org", "one", queued, payload())
    started = asyncio.Event()

    async def execute(job):
        started.set()
        await asyncio.Event().wait()

    first = TrainingWorker(jobs, execute=execute)
    second = TrainingWorker(TrainingJobs(tmp_path), execute=execute)
    await first.start()
    try:
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(RuntimeError, match="worker lock"):
            await second.start()
        assert jobs.get("org", "one", ident)["status"] == "running"
    finally:
        await first.close()
    assert jobs.get("org", "one", ident)["status"] == "failed"
    assert jobs.get("org", "one", queued)["status"] == "queued"


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "missing base",
        "candidate exists",
        "missing readback",
        "missing context",
        "import failed",
    ],
)
def test_actual_export_import_and_readback_are_required_without_overwriting_base(
    tmp_path, monkeypatch, failure
):
    from sagewai.admin import local_training_executor as bridge
    from sagewai.autopilot.curator.fine_tune import FineTuneExecutor, FineTuneResult

    model = tmp_path / "installed"
    model.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    gguf = output / "model.gguf"
    gguf.write_bytes(b"GGUFsynthetic export")
    candidate = "sagewai-trained-synthetic:latest"
    blob = "sha256:" + hashlib.sha256(gguf.read_bytes()).hexdigest()
    model_digest = "c" * 64
    trained, imported, paths = [], [], []

    def train(executor, job, dataset):
        assert executor.config.produce_gguf
        assert job.base_model == str(model)
        assert dataset.project_id == "one"
        trained.append(job)
        return FineTuneResult(
            status="completed", model_path=str(output), metrics={"gguf_path": str(gguf)}
        )

    monkeypatch.setattr(FineTuneExecutor, "execute", train)

    def respond(request):
        assert request.url.host == "127.0.0.1"
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            models = (
                []
                if failure == "missing base"
                else [{"name": "synthetic:base", "digest": "b" * 64}]
            )
            if failure == "candidate exists" or imported and failure != "missing readback":
                models.append({"name": candidate, "digest": model_digest})
            return httpx.Response(200, json={"models": models})
        if request.url.path.startswith("/api/blobs/"):
            assert request.url.path == "/api/blobs/" + blob
            assert request.content == gguf.read_bytes()
            return httpx.Response(201)
        if request.url.path == "/api/create":
            assert json.loads(request.content) == {
                "model": candidate,
                "files": {"model.gguf": blob},
                "stream": False,
            }
            imported.append(candidate)
            return httpx.Response(
                500 if failure == "import failed" else 200, json={"status": "success"}
            )
        if request.url.path == "/api/show":
            assert json.loads(request.content) == {"model": candidate}
            info = (
                {}
                if failure == "missing context"
                else {"general.architecture": "llama", "llama.context_length": 4096}
            )
            return httpx.Response(200, json={"model_info": info})
        raise AssertionError(request.url)

    real_client = httpx.Client
    monkeypatch.setattr(
        bridge.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    args = ({"id": str(uuid4()), "project_id": "one", **payload().model_dump()},)
    kwargs = {
        "model_path": model,
        "output_dir": output,
        "ollama_url": "http://127.0.0.1:11434",
        "candidate": candidate,
    }
    if failure:
        with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
            bridge.execute_local(*args, **kwargs)
    else:
        assert bridge.execute_local(*args, **kwargs) == {
            "candidate_model": candidate,
            "endpoint_url": kwargs["ollama_url"],
            "digest": "sha256:" + model_digest,
            "context_window": 4096,
        }
    if failure in ("missing base", "candidate exists"):
        assert trained == [] and imported == []
    assert "/api/pull" not in paths


def test_parent_hard_stop_terminates_only_its_training_group_before_import(tmp_path):
    ident = str(uuid4())
    model = tmp_path / "installed"
    model.mkdir()
    pid_file, imported = tmp_path / "child.pid", tmp_path / "imported"
    jobs_path = tmp_path / "jobs"
    parent_code = r'''
import asyncio, json, os, sys
from pathlib import Path
from sagewai.admin.local_training import TrainingJobs, TrainingRequest, TrainingWorker

async def main():
    root, ident, model, pid_file, imported = sys.argv[1:]
    os.environ["SAGEWAI_LOCAL_TRAINING_MODELS"] = json.dumps({"synthetic:base": model})
    jobs = TrainingJobs(Path(root))
    jobs.submit("org", "one", ident, TrainingRequest(
        dataset_id="synthetic", manifest_hash="a" * 64, base_model="synthetic:base",
        samples=[{"instruction": "Question", "output": "Answer"}]))
    real_spawn = asyncio.create_subprocess_exec
    async def child(*args, **kwargs):
        request = json.loads(Path(args[3]).read_text())
        code = """
import os, sys, time
from pathlib import Path
from sagewai.admin.local_training_executor import guard_parent
directory, ident = Path(sys.argv[4]), sys.argv[5]
snapshot = directory / 'model' / ident / 'dataset.jsonl'
snapshot.parent.mkdir(parents=True)
snapshot.write_text('synthetic private sample')
guard_parent(int(sys.argv[1]), directory=directory, ident=ident)
Path(sys.argv[2]).write_text(str(os.getpid()))
time.sleep(1)
Path(sys.argv[3]).write_text('imported')
"""
        return await real_spawn(sys.executable, "-c", code,
            str(request["parent_pid"]), pid_file, imported, request["output_dir"], ident, **kwargs)
    asyncio.create_subprocess_exec = child
    worker = TrainingWorker(jobs)
    await worker.start()
    await asyncio.Event().wait()

asyncio.run(main())
'''
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            parent_code,
            str(jobs_path),
            ident,
            str(model),
            str(pid_file),
            str(imported),
        ]
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 15
        while not pid_file.exists() and time.monotonic() < deadline:
            assert parent.poll() is None
            time.sleep(0.02)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        assert os.getpgid(child_pid) == child_pid
        parent.kill()
        parent.wait(timeout=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("The orphan trainer group did not exit after its parent was killed")
        assert foreign.poll() is None
        jobs = TrainingJobs(jobs_path)
        directory = jobs.directory("org", "one", ident)
        assert not (directory / "request.json").exists()
        assert not (directory / "model" / ident / "dataset.jsonl").exists()
        jobs.recover()
        assert jobs.get("org", "one", ident)["status"] == "failed"
        time.sleep(1.05)
        assert not imported.exists()
        assert jobs.get("org", "one", ident)["candidate_model"] is None
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=2)
        foreign.terminate()
        foreign.wait(timeout=2)
        if child_pid is not None:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_training_configuration_cannot_select_remote_endpoints_or_model_downloads(
    tmp_path, monkeypatch
):
    from sagewai.admin.local_training_executor import local_configuration

    monkeypatch.setenv(
        "SAGEWAI_LOCAL_TRAINING_MODELS", json.dumps({"synthetic:base": "model-from-the-hub"})
    )
    with pytest.raises(ValueError, match="existing absolute"):
        local_configuration()
    monkeypatch.setenv(
        "SAGEWAI_LOCAL_TRAINING_MODELS", json.dumps({"synthetic:base": str(tmp_path)})
    )
    for endpoint in [
        "https://example.test",
        "http://localhost:11434",
        "http://127.0.0.1:11434/remote",
        "http://user:secret@127.0.0.1:11434",
    ]:
        monkeypatch.setenv("SAGEWAI_LOCAL_TRAINING_OLLAMA_URL", endpoint)
        with pytest.raises(ValueError, match="loopback"):
            local_configuration()


@pytest.mark.asyncio
async def test_project_bound_tokens_cannot_forge_training_scope(tmp_path, monkeypatch):
    from sagewai.admin.api_token_store import ApiTokenStore
    from sagewai.admin.identity_store import IdentityStore
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.db.engine import create_engine

    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}")
    identity = IdentityStore(engine=engine)
    await identity.init()
    org = (await identity.bootstrap_org("Synthetic", "synthetic"))["id"]
    projects = [(await identity.create_project(org, name, name))["id"] for name in ("one", "two")]
    user = await identity.create_user(
        org, "member@example.test", password="synthetic password", role="org:member"
    )
    await identity.add_membership(org, user["id"], "project:member", project_id=projects[0])
    tokens = ApiTokenStore(engine=engine)
    await tokens.init()
    ctx = await identity.build_context(org, user["id"], project_id=projects[0])
    _, token = await tokens.create_for(
        ctx, name="Synthetic", scopes={"read", "write"}, project_id=projects[0]
    )
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(
        org_name="Synthetic", admin_email="admin@example.test", admin_password="synthetic password"
    )
    app = create_admin_serve_app(
        sf,
        identity_store=identity,
        api_token_store=tokens,
        training_jobs=TrainingJobs(tmp_path / "jobs"),
    )
    path = "/api/v1/training/jobs/" + str(uuid4())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
        ) as client:
            headers = {"Authorization": "Bearer " + token, "X-Project-ID": projects[0]}
            assert (
                await client.post(path, headers=headers, json=payload().model_dump())
            ).status_code == 200
            for method in ("get", "delete", "post"):
                response = await client.request(
                    method.upper(),
                    path,
                    headers=headers | {"X-Project-ID": projects[1]},
                    **({"json": payload().model_dump()} if method == "post" else {}),
                )
                assert response.status_code == 404
            assert (await client.get(path, headers=headers)).json()["project_id"] == projects[0]
    finally:
        await engine.dispose()
