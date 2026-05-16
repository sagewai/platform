#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 46 — Custom inference endpoint as tool / LLM (bring-your-own).

Closes Gap #8e of the inference spectrum. The audience-pin person —
a senior engineer at a 50-500 person SaaS — has *already* standardised
on an inference endpoint that isn't on Sagewai's shipped list (RunPod /
Modal / Colab / Vast.ai). They have a vLLM cluster on GKE, an Ollama
on a beefy mac mini, a TGI on HuggingFace Endpoints, a self-hosted
Triton, a Modal-served LoRA from Example 48, an AWS Bedrock proxy,
or a Lambda Labs / Paperspace / Vast.ai pod they spun up by hand.
Sagewai must integrate with what they have — not ask them to migrate.

This example proves two distinct integration shapes work end-to-end
on a clean machine, in 60 seconds, with no API key:

1. **As an LLM (LiteLLM-shaped passthrough).** Point ``UniversalAgent``
   at any OpenAI-compatible endpoint via ``api_base`` + ``api_key``.
   The agent's code is byte-identical to Example 02 (``02_tool_agent``).
   LiteLLM speaks OpenAI's protocol; vLLM / TGI / Ollama / Modal /
   Bedrock all expose it; Sagewai inherits compatibility for free.

2. **As a tool (MCP-style adapter).** Wrap a non-OpenAI inference
   endpoint — a domain-specific classifier, an embedding service, a
   structured-output SLM — as a Sagewai ``@tool``. The agent calls it
   the same way it calls any other tool, with auto-generated JSON
   Schema, same retry policy, same observability.

3. **Mix and match.** The same agent uses a custom LLM (cheap general
   reasoning) AND a custom tool (specialised classifier) in one run.
   Sagewai treats custom inference as first-class at every layer.

Pipeline::

    UniversalAgent ─litellm─▶ http://127.0.0.1:8765/v1/chat/completions
                              (synthetic vLLM-shaped server, in-proc)
                                              │
                              tool_call("classify_urgency", "...")
                                              │
                                              ▼
    UniversalAgent ─@tool───▶ http://127.0.0.1:8766/classify
                              (synthetic urgency classifier, in-proc)
                                              │
                                              ▼
                              {"urgency": "high", "score": 0.91}

Everything runs in-process. Two stdlib :class:`ThreadingHTTPServer`
instances stand in for the real cloud / vLLM / TGI endpoints. The
agent code that points at them is the *same code* you'd point at a
production inference URL — just swap the ``api_base``.

What's exercised:

- ``UniversalAgent(api_base=..., api_key=..., model="openai/my-lora")``
  — the LiteLLM-passthrough path. One kwarg switch turns Sagewai into
  a vLLM / TGI / Modal / Bedrock client
- ``@tool``-wrapped custom inference call — a domain classifier as a
  Sagewai-native tool, JSON Schema auto-generated, agent calls it
  through the same loop it uses for ``calculate`` or ``get_weather``
- Cleanup-triple-redundancy: ``try/finally`` + ``atexit`` + ``SIGTERM``
  / ``SIGINT`` handlers tear the spawned servers down even on panic.
  No leftover threads; no port hangs on the next run
- ``http.server.ThreadingHTTPServer`` — stdlib only. Zero-dep
  synthetic backends so a clean ``pip install sagewai`` runs the demo
  end-to-end. The companion README documents how to swap each
  synthetic endpoint for the real product (vLLM, Ollama, TGI, Modal,
  Bedrock) without changing the agent code

Requirements::

    pip install sagewai
    # Optional: ANTHROPIC_API_KEY (or any LiteLLM-compatible cloud key)
    # to demo the "cloud LLM, custom-tool" mix in scenario B. The
    # default scenario B path uses the synthetic OpenAI-compat server,
    # so a clean machine still runs the full demo.

Usage::

    # Default — runs all three scenarios against the synthetic backends
    python 46_custom_inference_as_tool.py

    # Hand-roll a different port (default 8765 LLM + 8766 tool)
    python 46_custom_inference_as_tool.py --llm-port 18765 --tool-port 18766

    # Skip a scenario for debugging
    python 46_custom_inference_as_tool.py --only mix
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sagewai.engines.universal import UniversalAgent
from sagewai.models.tool import tool

# ── Synthetic-endpoint knobs ──────────────────────────────────────

DEFAULT_LLM_PORT: int = 8765
DEFAULT_TOOL_PORT: int = 8766
HOST: str = "127.0.0.1"

# Model name the synthetic LLM advertises. Matches the audience-pin
# narrative: "I trained a LoRA on RunPod and serve it on Modal" — the
# agent points at any name; LiteLLM treats it as an OpenAI-compatible
# completion target.
LLM_MODEL_LABEL: str = "my-finetune"

# Three representative tickets covering the canonical urgency tiers.
# Same email-triage shape Examples 36 / 38 / 47 use, kept inline so
# this example doesn't depend on a sibling dataset.
DEMO_TICKETS: list[str] = [
    "Cannot log in. My account is locked. I have a deadline at 5pm.",
    "Would love a dark-mode option whenever you get to it. No rush.",
    "You charged me twice for the May invoice. Please refund the duplicate.",
]


# ── Helpers ──────────────────────────────────────────────────────


def _line(text: str = "", char: str = "─") -> None:
    if not text:
        print(char * 72)
    else:
        print(f"{char * 3} {text} {char * max(1, 68 - len(text))}")


def _pick_free_port(preferred: int) -> int:
    """Return ``preferred`` if free, else an ephemeral port the OS picks.

    Local dev machines often have stale processes squatting on 8765/
    8766; binding a random free port instead of failing keeps the
    "clean machine, 60 seconds" promise intact.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, preferred))
            return preferred
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((HOST, 0))
            return s.getsockname()[1]


def _classify_urgency(text: str) -> dict[str, Any]:
    """Pure-Python urgency classifier — the synthetic SLM's brain.

    The same content-routing rules Example 47's email-triage dataset
    encodes. In production this is your fine-tuned classifier; here
    it's deterministic so the demo's output is reproducible.
    """
    lower = text.lower()
    high_keywords = (
        "outage", "locked", "billing", "deadline", "mfa", "production",
        "auth outage", "duplicate", "refund", "page",
    )
    low_keywords = ("feature", "no rush", "whenever",)
    if any(kw in lower for kw in high_keywords):
        return {"urgency": "high", "score": 0.91, "reasoning": "high-keyword match"}
    if any(kw in lower for kw in low_keywords):
        return {"urgency": "low", "score": 0.88, "reasoning": "low-keyword match"}
    return {"urgency": "medium", "score": 0.62, "reasoning": "default tier"}


# ── Synthetic OpenAI-compatible LLM server ─────────────────────────
#
# Stands in for vLLM, TGI, Ollama (OpenAI-compat mode), Modal-served
# LoRA, AWS Bedrock (OpenAI-compat path). LiteLLM POSTs to
# /v1/chat/completions; we return the OpenAI-shaped envelope.

class _OpenAICompatLLMHandler(BaseHTTPRequestHandler):
    """Minimal /v1/chat/completions handler.

    Behaviour rules (mirror what a fine-tuned classifier-LoRA would do):

    1. If the messages contain any tool-result already (the second
       round of the agent loop), return a final content reply that
       summarises the tool's verdict.
    2. Otherwise, if any tool definition includes ``classify_urgency``,
       emit a tool_call asking the agent to invoke it on the latest
       user message.
    3. Otherwise (no tools available), return a one-line plain text
       reply.
    """

    server_version = "SagewaiSyntheticLLM/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
        # Silence the per-request access log so the demo output stays
        # focused on agent traffic.
        return

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 — http.server convention
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": f"unknown path {self.path}"}})
            return
        body = self._read_body()
        messages: list[dict[str, Any]] = body.get("messages", []) or []
        tools: list[dict[str, Any]] = body.get("tools", []) or []
        tool_names = {
            (t.get("function") or {}).get("name") for t in tools
        }
        last_user: str = ""
        already_called_tool = False
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                last_user = msg.get("content") or last_user
            if role == "tool":
                already_called_tool = True

        envelope: dict[str, Any] = {
            "id": f"chatcmpl-synthetic-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": LLM_MODEL_LABEL,
            "choices": [],
            "usage": {
                "prompt_tokens": sum(
                    len((m.get("content") or "").split()) for m in messages
                ),
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

        # Branch 1: agent already invoked the tool — emit final reply.
        if already_called_tool:
            tool_msg = next(
                (m for m in reversed(messages) if m.get("role") == "tool"),
                None,
            )
            verdict_label = "unknown"
            if tool_msg:
                content = tool_msg.get("content") or ""
                try:
                    parsed = json.loads(content)
                    verdict_label = parsed.get("urgency", "unknown")
                except (json.JSONDecodeError, TypeError):
                    pass
            content_text = (
                f"Triage complete: classifier reported urgency = "
                f"{verdict_label}."
            )
            envelope["choices"] = [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content_text},
                    "finish_reason": "stop",
                },
            ]
            envelope["usage"]["completion_tokens"] = len(content_text.split())
            self._send_json(200, envelope)
            return

        # Branch 2: tool available — request a tool call.
        if "classify_urgency" in tool_names:
            tool_call_id = f"call-{int(time.time() * 1000)}"
            envelope["choices"] = [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": "classify_urgency",
                                    "arguments": json.dumps(
                                        {"text": last_user},
                                    ),
                                },
                            },
                        ],
                    },
                    "finish_reason": "tool_calls",
                },
            ]
            self._send_json(200, envelope)
            return

        # Branch 3: no tools — emit a one-line plain reply derived
        # from the local classifier so the demo's output is honest.
        verdict = _classify_urgency(last_user)
        reply = (
            f"Reading the ticket as written, urgency is "
            f"{verdict['urgency']} ({verdict['reasoning']})."
        )
        envelope["choices"] = [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            },
        ]
        envelope["usage"]["completion_tokens"] = len(reply.split())
        self._send_json(200, envelope)

    def do_GET(self) -> None:  # noqa: N802 — http.server convention
        # /v1/models — vLLM / TGI expose this; some LiteLLM probes
        # call it. Return a minimal OpenAI-compat models list.
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": LLM_MODEL_LABEL,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "synthetic",
                        },
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": f"unknown path {self.path}"}})


# ── Synthetic domain-classifier server ────────────────────────────
#
# Stands in for the customer's "we already host an SLM that returns
# JSON" endpoint. Could be a sentiment classifier, a PII detector, a
# structured-output extraction service, anything that is not OpenAI-
# shaped but the agent should still be able to call.

class _ClassifierHandler(BaseHTTPRequestHandler):
    """Minimal POST /classify handler returning ``_classify_urgency`` output."""

    server_version = "SagewaiSyntheticClassifier/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
        return

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 — http.server convention
        if self.path.rstrip("/") != "/classify":
            self._send_json(404, {"error": {"message": f"unknown path {self.path}"}})
            return
        body = self._read_body()
        text = body.get("text", "") or ""
        verdict = _classify_urgency(text)
        self._send_json(200, verdict)


# ── Server lifecycle ─────────────────────────────────────────────


@dataclass
class _ServerHandle:
    """Tracks one in-process HTTP server so cleanup can find it."""

    name: str
    server: ThreadingHTTPServer
    thread: threading.Thread
    port: int


_ACTIVE_SERVERS: list[_ServerHandle] = []
_TEARDOWN_DONE: bool = False


def _start_server(
    *, name: str, port: int, handler_cls: type[BaseHTTPRequestHandler],
) -> _ServerHandle:
    """Bind an HTTP server on ``HOST:port`` and run it in a daemon thread."""
    server = ThreadingHTTPServer((HOST, port), handler_cls)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, name=f"sagewai-ex46-{name}",
        daemon=True,
    )
    thread.start()
    handle = _ServerHandle(name=name, server=server, thread=thread, port=port)
    _ACTIVE_SERVERS.append(handle)
    return handle


def _teardown_servers() -> None:
    """Shutdown every active in-process server. Idempotent."""
    global _TEARDOWN_DONE  # noqa: PLW0603
    if _TEARDOWN_DONE:
        return
    for handle in _ACTIVE_SERVERS:
        try:
            handle.server.shutdown()
            handle.server.server_close()
            handle.thread.join(timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] {handle.name} teardown returned: {exc}")
    _ACTIVE_SERVERS.clear()
    _TEARDOWN_DONE = True


def _register_signal_handlers() -> None:
    """Wire SIGTERM + SIGINT + atexit so a panic doesn't leak threads."""

    def _on_signal(signum: int, _frame: object) -> None:
        print(f"\n  [signal {signum}] caught — tearing down synthetic servers.")
        _teardown_servers()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    atexit.register(_teardown_servers)


def _wait_until_ready(*, host: str, port: int, timeout_s: float = 3.0) -> None:
    """Block until the synthetic server answers a TCP probe (or raise)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(
        f"Synthetic server at {host}:{port} did not answer within "
        f"{timeout_s:.1f}s",
    )


# ── @tool wrapping the custom classifier endpoint ─────────────────


@dataclass
class _ToolEndpointConfig:
    """Holds the URL the @tool POSTs to. Set at runtime by main()."""

    url: str = ""


_TOOL_CONFIG = _ToolEndpointConfig()


@tool
async def classify_urgency(text: str) -> str:
    """Classify the urgency of a customer-support ticket via a custom
    inference endpoint.

    Returns a JSON string with shape ``{"urgency": "...", "score": ...,
    "reasoning": "..."}``. Calls a non-OpenAI HTTP service — exactly
    the shape an audience-pin person would have for an internal
    classifier (sentiment, PII, structured extraction, etc.).

    Args:
        text: The full ticket body to classify.
    """
    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        _TOOL_CONFIG.url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _post() -> str:
        with urllib.request.urlopen(request, timeout=5.0) as resp:
            return resp.read().decode("utf-8")

    return await asyncio.to_thread(_post)


# ── Demos ─────────────────────────────────────────────────────────


@dataclass
class _DemoContext:
    """Shared context passed to each demo function."""

    llm_url: str
    tool_url: str
    transcripts: list[dict[str, str]] = field(default_factory=list)


async def demo_llm_shape(ctx: _DemoContext) -> None:
    """Scenario A — the custom endpoint is the LLM.

    Same agent shape as Example 02, but the LLM lives behind ``api_base``
    instead of an Anthropic / OpenAI key. Proves a self-hosted vLLM /
    TGI / Modal endpoint plugs in by changing one line.
    """
    _line(" Scenario A — custom endpoint as the LLM ")
    print()
    print("  Agent points at the synthetic OpenAI-compat server.")
    print("  Same code as Example 02 (`02_tool_agent`); the only diff")
    print("  is the api_base + api_key kwargs on UniversalAgent.")
    print()

    agent = UniversalAgent(
        name="custom-llm-only",
        model=f"openai/{LLM_MODEL_LABEL}",
        api_base=ctx.llm_url,
        api_key="not-required-for-self-hosted",
        system_prompt=(
            "You read customer-support tickets and reply with a single "
            "sentence describing the urgency."
        ),
        max_iterations=2,
    )

    for ticket in DEMO_TICKETS[:1]:
        print(f"  Ticket : {ticket}")
        reply = await agent.chat(ticket)
        print(f"  Reply  : {reply}")
        ctx.transcripts.append(
            {"scenario": "A", "ticket": ticket, "reply": reply},
        )
    print()


async def demo_tool_shape(ctx: _DemoContext) -> None:
    """Scenario B — the custom endpoint is a tool.

    The agent uses the synthetic OpenAI-compat LLM as its reasoning
    engine (no API key required) AND calls the custom classifier
    endpoint via ``@tool``. The tool-shape pattern works identically
    when the LLM is Anthropic / OpenAI / Bedrock — only the LLM
    endpoint changes.
    """
    _line(" Scenario B — custom endpoint as a tool ")
    print()
    print("  Agent's LLM = synthetic OpenAI-compat (no API key needed).")
    print("  Agent's tool = synthetic /classify endpoint via @tool.")
    print()

    agent = UniversalAgent(
        name="llm-plus-custom-tool",
        model=f"openai/{LLM_MODEL_LABEL}",
        api_base=ctx.llm_url,
        api_key="not-required-for-self-hosted",
        tools=[classify_urgency],
        system_prompt=(
            "You read customer-support tickets. Use the classify_urgency "
            "tool to determine the urgency, then respond with one "
            "sentence summarising the verdict."
        ),
        max_iterations=4,
    )

    for ticket in DEMO_TICKETS[:1]:
        print(f"  Ticket : {ticket}")
        reply = await agent.chat(ticket)
        print(f"  Reply  : {reply}")
        ctx.transcripts.append(
            {"scenario": "B", "ticket": ticket, "reply": reply},
        )
    print()


async def demo_mix_and_match(ctx: _DemoContext) -> None:
    """Scenario C — custom LLM AND custom tool together, multiple tickets.

    The full bring-your-own loop. Cheap reasoning on the custom LLM,
    specialised classification on the custom tool, multiple tickets
    in one agent session. This is the production shape an audience-pin
    person ships.
    """
    _line(" Scenario C — mix-and-match: custom LLM + custom tool ")
    print()
    print("  Same agent runs three tickets back-to-back. Custom LLM does")
    print("  the reasoning; custom tool does the classification.")
    print()

    agent = UniversalAgent(
        name="mix-and-match",
        model=f"openai/{LLM_MODEL_LABEL}",
        api_base=ctx.llm_url,
        api_key="not-required-for-self-hosted",
        tools=[classify_urgency],
        system_prompt=(
            "You triage customer-support tickets. For each ticket, call "
            "classify_urgency, then reply in one sentence with the "
            "verdict and a brief justification."
        ),
        max_iterations=4,
    )

    for ticket in DEMO_TICKETS:
        print(f"  Ticket : {ticket}")
        reply = await agent.chat(ticket)
        print(f"  Reply  : {reply}")
        ctx.transcripts.append(
            {"scenario": "C", "ticket": ticket, "reply": reply},
        )
        print()


# ── Proof block ───────────────────────────────────────────────────


def print_proof(ctx: _DemoContext) -> None:
    """Print the summary table of every transcript captured."""
    print(
        f"  Total agent turns recorded : {len(ctx.transcripts)}  "
        "(scenarios A + B + C)"
    )
    print(f"  Custom LLM endpoint        : {ctx.llm_url}")
    print(f"  Custom tool endpoint       : {ctx.tool_url}")
    print(f"  External services used     : 0 (everything is in-proc)")
    print(f"  API keys required          : 0")
    print()
    print("  ── Transcript ──")
    print()
    for entry in ctx.transcripts:
        scenario = entry["scenario"]
        ticket = entry["ticket"]
        reply = entry["reply"]
        ticket_short = ticket if len(ticket) <= 56 else ticket[:53] + "…"
        reply_short = reply if len(reply) <= 56 else reply[:53] + "…"
        print(f"  [{scenario}] {ticket_short}")
        print(f"      → {reply_short}")
    print()


def print_configuration_pointer() -> None:
    """Tell the reader where to find the full configuration matrix."""
    print("  ── Pointing the agent at YOUR endpoint ──")
    print()
    print("  This demo uses synthetic stdlib HTTP servers for")
    print("  reproducibility. To swap them for real production endpoints,")
    print("  change the api_base / @tool URL — agent code stays identical.")
    print()
    print("  Configuration matrix (vLLM, Ollama, TGI, Modal, AWS Bedrock,")
    print("  Lambda Labs, Vast.ai, Paperspace) lives in the companion")
    print("  README: 46_custom_inference_as_tool.md")
    print()


# ── main ──────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Example 46 — custom inference as tool / LLM.",
    )
    parser.add_argument(
        "--llm-port", type=int, default=DEFAULT_LLM_PORT,
        help=f"Port for the synthetic OpenAI-compat LLM server "
             f"(default {DEFAULT_LLM_PORT}).",
    )
    parser.add_argument(
        "--tool-port", type=int, default=DEFAULT_TOOL_PORT,
        help=f"Port for the synthetic /classify endpoint "
             f"(default {DEFAULT_TOOL_PORT}).",
    )
    parser.add_argument(
        "--only", choices=("llm", "tool", "mix", "all"), default="all",
        help="Run only one scenario; default is all three.",
    )
    args = parser.parse_args()

    _line()
    print(" Sagewai — custom inference as tool / LLM (example 46, Gap #8e)")
    _line()
    print()

    # ── 1. Probe + bind synthetic servers ──────────────────────
    _line(" 1. Boot synthetic in-proc endpoints ")
    print()
    _register_signal_handlers()

    llm_port = _pick_free_port(args.llm_port)
    tool_port = _pick_free_port(args.tool_port)
    if llm_port != args.llm_port:
        print(f"  [info] port {args.llm_port} busy — using {llm_port} for LLM server.")
    if tool_port != args.tool_port:
        print(f"  [info] port {args.tool_port} busy — using {tool_port} for tool server.")

    _start_server(
        name="llm", port=llm_port, handler_cls=_OpenAICompatLLMHandler,
    )
    _start_server(
        name="tool", port=tool_port, handler_cls=_ClassifierHandler,
    )

    llm_url = f"http://{HOST}:{llm_port}/v1"
    tool_url = f"http://{HOST}:{tool_port}/classify"
    _TOOL_CONFIG.url = tool_url

    try:
        _wait_until_ready(host=HOST, port=llm_port)
        _wait_until_ready(host=HOST, port=tool_port)
    except TimeoutError as exc:
        print(f"  [error] {exc}")
        _teardown_servers()
        sys.exit(1)

    print(f"  Synthetic OpenAI-compat LLM : {llm_url}/chat/completions")
    print(f"  Synthetic /classify tool    : {tool_url}")
    print(f"  Cleanup wired               : try/finally + atexit + SIGTERM")
    print()

    ctx = _DemoContext(llm_url=llm_url, tool_url=tool_url)

    try:
        # ── 2. Run scenarios ────────────────────────────────────
        if args.only in ("llm", "all"):
            await demo_llm_shape(ctx)
        if args.only in ("tool", "all"):
            await demo_tool_shape(ctx)
        if args.only in ("mix", "all"):
            await demo_mix_and_match(ctx)

        # ── 3. Proof block ──────────────────────────────────────
        _line(" The proof — agent transcripts ")
        print()
        print_proof(ctx)

        # ── 4. Pointer to the configuration matrix ──────────────
        _line(" Configuration matrix (real endpoints) ")
        print()
        print_configuration_pointer()

        # ── 5. Pillar pitch ─────────────────────────────────────
        _line(" The training-loop pillar (and the SDK pillar) ")
        print()
        print("  Custom inference is first-class in Sagewai. Whatever")
        print("  endpoint you already pay for — vLLM, Ollama, TGI, Modal,")
        print("  Bedrock, Lambda Labs, Vast.ai, Paperspace, your own GKE")
        print("  vLLM cluster — plugs in two ways:")
        print()
        print("    * As an LLM via api_base + api_key (LiteLLM-shaped).")
        print("    * As a tool via @tool (MCP-style adapter).")
        print()
        print("  The agent code does not change. Optionality is the brand.")
        print()
    finally:
        _teardown_servers()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _teardown_servers()
        sys.exit(130)
