# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Offline FineTuneExecutor bridge; completion requires an imported Ollama model."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .local_training import scrub_inputs


def guard_parent(parent_pid: int, *, directory: Path, ident: str):
    """Stop only this isolated trainer group if its service parent disappears."""
    group = os.getpid()
    if os.getpgrp() != group:
        raise RuntimeError("Local training requires its own process group")

    def stop():
        try:
            scrub_inputs(directory, ident)
        finally:
            if os.getpgrp() == group:
                os.killpg(group, signal.SIGKILL)
            os._exit(1)

    if os.getppid() != parent_pid:
        stop()

    def watch():
        while os.getppid() == parent_pid:
            time.sleep(0.1)
        stop()

    threading.Thread(target=watch, daemon=True).start()


def local_configuration():
    """Resolve only owner-configured installed directories and a loopback server."""
    raw = json.loads(os.environ.get("SAGEWAI_LOCAL_TRAINING_MODELS", "{}"))
    if not isinstance(raw, dict):
        raise ValueError(
            "SAGEWAI_LOCAL_TRAINING_MODELS must map catalog names to installed directories"
        )
    models = {}
    for name, directory in raw.items():
        path = Path(directory)
        if not name or not path.is_absolute() or not path.is_dir():
            raise ValueError("Local training models require existing absolute model directories")
        models[name] = path.resolve()
    endpoint = os.environ.get("SAGEWAI_LOCAL_TRAINING_OLLAMA_URL", "http://127.0.0.1:11434").rstrip(
        "/"
    )
    parsed = urlsplit(endpoint)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        not loopback
        or parsed.scheme not in ("http", "https")
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Local training requires a configured literal loopback Ollama endpoint")
    return models, endpoint


def _tag(name):
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def ollama_models(endpoint):
    with httpx.Client(
        base_url=endpoint, trust_env=False, follow_redirects=False, timeout=120
    ) as client:
        response = client.get("/api/tags")
        response.raise_for_status()
        return {item["name"]: item["digest"] for item in response.json()["models"]}


def execute_local(job, *, model_path: Path, output_dir: Path, ollama_url: str, candidate: str):
    """Train one local directory and import only its actual GGUF under a new name."""
    from sagewai.autopilot.curator.fine_tune import FineTuneConfig, FineTuneExecutor
    from sagewai.autopilot.curator.types import FineTuneJob, TrainingDataset

    installed = ollama_models(ollama_url)
    if _tag(job["base_model"]) not in installed:
        raise RuntimeError(
            "The configured base model is not installed in local Ollama; downloading is disabled"
        )
    if _tag(candidate) == _tag(job["base_model"]) or _tag(candidate) in installed:
        raise RuntimeError("The training candidate would overwrite an installed model")
    executor = FineTuneExecutor(
        FineTuneConfig(output_dir=str(output_dir / "model"), produce_gguf=True)
    )
    result = executor.execute(
        FineTuneJob(
            job_id=job["id"],
            dataset_id=job["dataset_id"],
            project_id=job["project_id"],
            base_model=str(model_path),
        ),
        TrainingDataset(
            dataset_id=job["dataset_id"],
            project_id=job["project_id"],
            format="alpaca",
            samples=job["samples"],
        ),
    )
    if result.status != "completed":
        raise RuntimeError(result.reason or "No local training backend completed the job")
    exported = result.metrics.get("gguf_path")
    if not exported:
        raise RuntimeError(
            result.metrics.get("gguf_export_error")
            or "Training produced no GGUF export; no candidate was served"
        )
    gguf = Path(exported).resolve()
    if not gguf.is_relative_to(output_dir.resolve()) or not gguf.is_file():
        raise RuntimeError("The GGUF export is missing or outside this job's output directory")
    with gguf.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise RuntimeError("Training output is not a GGUF file")
        stream.seek(0)
        hashed = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hashed.update(block)
        digest = "sha256:" + hashed.hexdigest()
        stream.seek(0)
        with httpx.Client(
            base_url=ollama_url, trust_env=False, follow_redirects=False, timeout=120
        ) as client:
            response = client.post("/api/blobs/" + digest, content=stream)
            response.raise_for_status()
            response = client.post(
                "/api/create",
                json={"model": candidate, "files": {"model.gguf": digest}, "stream": False},
            )
            response.raise_for_status()
            if response.json().get("status") != "success":
                raise RuntimeError("Local Ollama did not confirm candidate import")
    readback = ollama_models(ollama_url).get(_tag(candidate), "")
    if not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", readback):
        raise RuntimeError("The imported candidate has no verified local Ollama digest")
    with httpx.Client(
        base_url=ollama_url, trust_env=False, follow_redirects=False, timeout=120
    ) as client:
        response = client.post("/api/show", json={"model": candidate})
        response.raise_for_status()
        info = response.json().get("model_info", {})
    context_window = info.get(str(info.get("general.architecture", "")) + ".context_length")
    if type(context_window) is not int or context_window <= 0:
        raise RuntimeError("The imported candidate has no verified context window")
    return {
        "candidate_model": _tag(candidate),
        "endpoint_url": ollama_url,
        "digest": "sha256:" + readback.removeprefix("sha256:"),
        "context_window": context_window,
    }


def main():
    request, target = Path(sys.argv[1]), Path(sys.argv[2])
    args = json.loads(request.read_text())
    try:
        guard_parent(args["parent_pid"], directory=request.parent, ident=args["job"]["id"])
        result = execute_local(
            args["job"],
            model_path=Path(args["model_path"]),
            output_dir=Path(args["output_dir"]),
            ollama_url=args["ollama_url"],
            candidate=args["candidate"],
        )
    except Exception as exc:
        result = {"error": str(exc) or type(exc).__name__}
    finally:
        scrub_inputs(request.parent, args["job"]["id"])
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result))
    temporary.chmod(0o600)
    temporary.replace(target)


if __name__ == "__main__":
    main()
