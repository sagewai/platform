# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""MissionDriver telemetry → :class:`MissionRunBus` event translator.

This module is the load-bearing concurrency seam in Plan H.  It runs a
mission via :class:`~sagewai.autopilot.controller.driver.MissionDriver`,
walks the resulting :class:`MissionRunResult`, and fans out per-step
events to a :class:`~sagewai.admin.autopilot_run_bus.MissionRunBus`
that powers the SSE live-trace endpoint.

Dispatch path
-------------
The Plan H design doc sketches a streaming driver (``driver.stream()``)
but that method does not exist in the SDK; the production driver
exposes only ``async def execute(mission)``.  We therefore await the
final :class:`MissionRunResult` and walk its ``steps`` post-hoc.  This
preserves the wrapper-extension point (Plans I/J/K can swap the
per-step emitter for one that surfaces sandbox / fleet / sealed
events) without forcing a streaming protocol onto every backend.

Wrapper extension point (Plans I/J/K)
-------------------------------------
The ``step_emitter`` parameter is the future seam for Plans I, J, K
(Fleet, Sandbox, Sealed wrappers).  Each may pass an alternate
emitter that wraps tool calls in additional telemetry events
(sandbox start/finish, fleet dispatch, sealed audit).  The default
emitter, :func:`_default_step_emitter`, publishes the canonical
``agent.*`` events documented in the design.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sagewai.admin.autopilot_run_bus import MissionRunBus
from sagewai.autopilot.controller.types import MissionRunResult, StepResult


# ── envelope helper ────────────────────────────────────────────────


def _ev(kind: str, mission_id: str, run_id: str, **payload: Any) -> dict[str, Any]:
    """Build an event envelope with the canonical envelope fields.

    Every event published to a :class:`MissionRunBus` carries:

    * ``event_id`` — uuid4 hex (unique across the whole stream).
    * ``ts`` — ISO-8601 UTC timestamp (with timezone suffix).
    * ``mission_id`` — the mission this event belongs to.
    * ``run_id`` — the specific run inside that mission.
    * ``kind`` — the event kind (``mission.started`` / ``agent.*`` / …).

    The remaining keyword arguments are merged in as the per-kind
    payload.  Reserved envelope keys cannot be overridden; passing
    one in ``payload`` will simply be shadowed by the envelope fields.
    """
    base: dict[str, Any] = {
        "event_id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(),
        "mission_id": mission_id,
        "run_id": run_id,
        "kind": kind,
    }
    # Envelope keys win — payload may not override the canonical fields.
    for k, v in payload.items():
        if k not in base:
            base[k] = v
    return base


# ── step-emitter protocol ──────────────────────────────────────────


@runtime_checkable
class StepEmitter(Protocol):
    """Protocol for per-step event emitters.

    Plans I/J/K (Fleet / Sandbox / Sealed) implement this to wrap
    tool calls in additional telemetry events (e.g. sandbox lifecycle,
    fleet worker dispatch, sealed audit trail) without re-implementing
    the canonical mission-level event sequence.

    The emitter is responsible for publishing all per-step events for
    a single :class:`StepResult` and returning the cost contribution
    that should be added to the mission's running total.
    """

    async def __call__(
        self,
        *,
        bus: MissionRunBus,
        mission_id: str,
        run_id: str,
        step: StepResult,
    ) -> float: ...


# ── default per-step emitter ───────────────────────────────────────


async def _default_step_emitter(
    *,
    bus: MissionRunBus,
    mission_id: str,
    run_id: str,
    step: StepResult,
) -> float:
    """Default per-step emitter — publishes the canonical ``agent.*`` events.

    Sequence per step:

    1. ``agent.started`` (always).
    2. ``agent.tool_call`` for each name in ``step.tool_calls`` (if any).
    3. ``agent.tool_result`` for each name (only if the step succeeded).
    4. ``agent.tool_failed`` for each name (only if the step failed).
    5. ``agent.llm_call`` (only if ``step.telemetry`` is present).
    6. ``agent.finished`` (always).

    Returns the step's cost contribution: ``step.telemetry.cost_usd``
    when telemetry is present, ``0.0`` otherwise.
    """
    # 1. agent.started
    started_payload: dict[str, Any] = {"node_id": step.node_id, "status": step.status}
    if step.telemetry is not None:
        started_payload["model_used"] = step.telemetry.model_used
    await bus.publish(mission_id, _ev("agent.started", mission_id, run_id, **started_payload))

    # 2-4. tool events
    failed = step.status == "failed"
    if step.tool_calls:
        # 2. tool_call for every tool name
        for tool in step.tool_calls:
            await bus.publish(
                mission_id,
                _ev("agent.tool_call", mission_id, run_id, node_id=step.node_id, tool=tool),
            )
        # 3 / 4. tool_result on success, tool_failed on failure
        if failed:
            # MissionRunResult.error is the canonical failure string but
            # individual prior steps don't carry it; the caller injects it
            # via _set_step_error before calling us.  For steps without an
            # injected error, fall back to "step_failed".
            err = getattr(step, "_observer_error", None) or "step_failed"
            for tool in step.tool_calls:
                await bus.publish(
                    mission_id,
                    _ev(
                        "agent.tool_failed",
                        mission_id,
                        run_id,
                        node_id=step.node_id,
                        tool=tool,
                        error=err,
                    ),
                )
        else:
            for tool in step.tool_calls:
                await bus.publish(
                    mission_id,
                    _ev(
                        "agent.tool_result",
                        mission_id,
                        run_id,
                        node_id=step.node_id,
                        tool=tool,
                    ),
                )

    # 5. agent.llm_call when telemetry present
    if step.telemetry is not None:
        tel = step.telemetry
        await bus.publish(
            mission_id,
            _ev(
                "agent.llm_call",
                mission_id,
                run_id,
                node_id=step.node_id,
                model=tel.model_used,
                input_tokens=tel.input_tokens,
                output_tokens=tel.output_tokens,
                cost_usd=tel.cost_usd,
                latency_ms=tel.latency_ms,
            ),
        )

    # 6. agent.finished
    finished_payload: dict[str, Any] = {
        "node_id": step.node_id,
        "status": step.status,
        "output_preview": step.output_preview,
        "output": step.output,
    }
    await bus.publish(mission_id, _ev("agent.finished", mission_id, run_id, **finished_payload))

    return step.telemetry.cost_usd if step.telemetry is not None else 0.0


# ── public entry point ─────────────────────────────────────────────


def _blueprint_name(blueprint: Any) -> str | None:
    """Return ``blueprint.name`` if present, falling back to ``blueprint.title``.

    The autopilot :class:`Blueprint` model uses ``title`` as the
    human-readable label; tests and external callers may pass a
    duck-typed object with ``name``.  Accept either to keep the
    observer robust.
    """
    name = getattr(blueprint, "name", None)
    if name:
        return name
    return getattr(blueprint, "title", None)


def _attach_step_error(step: StepResult, error: str | None) -> StepResult:
    """Smuggle the mission-level error onto a step for the default emitter.

    :class:`StepResult` is a frozen pydantic model so we can't set an
    attribute directly.  Use ``object.__setattr__`` on a private
    ``_observer_error`` slot read by :func:`_default_step_emitter`.
    Custom emitters can ignore this and read ``MissionRunResult.error``
    themselves.
    """
    if error is not None:
        try:
            object.__setattr__(step, "_observer_error", error)
        except (AttributeError, TypeError):
            # Pydantic v2 frozen models permit object.__setattr__ for
            # non-field attributes; if a future version blocks it we
            # fall through and the emitter uses the "step_failed"
            # fallback.
            pass
    return step


async def run_mission_with_observer(
    *,
    bus: MissionRunBus,
    mission_id: str,
    run_id: str,
    blueprint: Any,
    mission: Any,
    driver: Any,
    step_emitter: StepEmitter | None = None,
) -> dict[str, Any]:
    """Run *mission* via *driver* and fan-out per-step events to *bus*.

    Sequence:

    1. Publish ``mission.started`` with the blueprint's id and name.
    2. Await ``driver.execute(mission)``.  Any exception is caught and
       converted to a failed ``mission.finished`` event.  The mission
       must already be in the SCHEDULED state — that's the caller's
       responsibility, not the observer's.
    3. For each step in ``result.steps``, call *step_emitter* (or
       :func:`_default_step_emitter` if ``step_emitter`` is ``None``).
       The emitter returns a cost contribution that is summed into the
       mission's ``total_cost_usd``.
    4. Publish ``mission.finished`` carrying ``status``,
       ``total_cost_usd`` (rounded to 6 decimals), ``total_duration_ms``,
       ``output`` (final step's ``output`` or ``output_preview``), and
       ``error`` (``None`` on success).

    Returns a summary dict with the same keys as ``mission.finished``
    plus ``step_count``.
    """
    emit = step_emitter if step_emitter is not None else _default_step_emitter

    # 1. mission.started — first event on the bus.
    await bus.publish(
        mission_id,
        _ev(
            "mission.started",
            mission_id,
            run_id,
            blueprint_id=getattr(blueprint, "id", None),
            blueprint_name=_blueprint_name(blueprint),
        ),
    )

    t0 = time.monotonic()

    # 2. Run the driver.  Any exception → failed mission.finished.
    try:
        result = await driver.execute(mission)
    except BaseException as exc:  # noqa: BLE001 — surface every failure as a finished event
        duration_ms = int((time.monotonic() - t0) * 1000)
        error_str = f"{type(exc).__name__}: {exc}"
        finished_payload = {
            "status": "failed",
            "total_cost_usd": 0.0,
            "total_duration_ms": duration_ms,
            "output": None,
            "error": error_str,
        }
        await bus.publish(
            mission_id,
            _ev("mission.finished", mission_id, run_id, **finished_payload),
        )
        return {**finished_payload, "step_count": 0}

    # 3. Walk steps via the emitter.
    total_cost = 0.0
    steps = tuple(result.steps)
    final_step_error = result.error
    for idx, step in enumerate(steps):
        # Attach the mission-level error onto the *failing* step so the
        # default emitter can stamp it on per-tool agent.tool_failed
        # events.  Prior (non-final) failed steps fall back to
        # "step_failed" inside the emitter.
        if step.status == "failed" and idx == len(steps) - 1 and final_step_error:
            _attach_step_error(step, final_step_error)
        contribution = await emit(
            bus=bus,
            mission_id=mission_id,
            run_id=run_id,
            step=step,
        )
        try:
            total_cost += float(contribution)
        except (TypeError, ValueError):
            pass

    # 4. mission.finished — last event on the bus.
    duration_ms = int((time.monotonic() - t0) * 1000)
    final_output: str | None = None
    if steps:
        final = steps[-1]
        final_output = final.output if final.output is not None else final.output_preview

    finished_payload = {
        "status": result.status,
        "total_cost_usd": round(total_cost, 6),
        "total_duration_ms": duration_ms,
        "output": final_output,
        "error": result.error,
    }
    await bus.publish(
        mission_id,
        _ev("mission.finished", mission_id, run_id, **finished_payload),
    )
    return {**finished_payload, "step_count": len(steps)}


__all__ = [
    "StepEmitter",
    "_default_step_emitter",
    "_ev",
    "run_mission_with_observer",
]
