#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fleet ``--exec`` handler — run an agent on a claimed task.

``sagewai fleet run --exec`` invokes this once per claimed task. The worker pipes
the task JSON in on **stdin**; this script runs a ``UniversalAgent`` with the
task's message and prints the agent's reply to **stdout** — which the worker then
reports back to the gateway as the task ``output``. A non-zero exit is reported as
``failed`` (stderr becomes the error).

Task payload shape (set by ``sagewai fleet enqueue``)::

    {"run_id": "...", "model": "gpt-4o-mini",
     "payload": {"agent": "helper", "message": "...", "model": "gpt-4o-mini"}}

Provider credential: the worker daemon scrubs ambient secrets from this process's
environment (allowlist), so your LLM key is NOT inherited. Supply it explicitly in
the ``--exec`` command, e.g.::

    sagewai fleet run --name w1 --models gpt-4o-mini \\
        --exec 'OPENAI_API_KEY=sk-... python ./agent_task_handler.py'

or source it from a file the operator controls::

    --exec 'set -a; . /etc/sagewai/task.env; set +a; python ./agent_task_handler.py'
"""

from __future__ import annotations

import asyncio
import json
import sys

from sagewai.engines.universal import UniversalAgent


def main() -> int:
    try:
        task = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("agent_task_handler: no / invalid task JSON on stdin", file=sys.stderr)
        return 1

    payload = task.get("payload") or {}
    message = payload.get("message")
    if not message:
        print("agent_task_handler: task payload has no 'message'", file=sys.stderr)
        return 1

    model = payload.get("model") or task.get("model") or "gpt-4o-mini"
    agent = UniversalAgent(name=payload.get("agent", "worker-agent"), model=model)
    response = asyncio.run(agent.chat(message))
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
