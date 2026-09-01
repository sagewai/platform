# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Native runtime capability discovery tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from sagewai.work.runtime_capabilities import (
    RefreshingCodexRuntime,
    RuntimeCapabilityProbeError,
    parse_claude_initialize,
    parse_codex_models,
    select_codex_task_configuration,
    select_runtime_configuration,
)


def _codex_catalog() -> str:
    return json.dumps(
        {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low"},
                        {"effort": "medium"},
                        {"effort": "high"},
                        {"effort": "xhigh"},
                    ],
                    "visibility": "list",
                    "supported_in_api": True,
                    "priority": 7,
                },
                {
                    "slug": "gpt-5.6-sol",
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [
                        {"effort": "low"},
                        {"effort": "medium"},
                        {"effort": "high"},
                        {"effort": "xhigh"},
                        {"effort": "max"},
                        {"effort": "ultra"},
                    ],
                    "visibility": "list",
                    "supported_in_api": True,
                    "priority": 1,
                },
                {
                    "slug": "hidden-model",
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [{"effort": "low"}],
                    "visibility": "hidden",
                    "supported_in_api": True,
                    "priority": 0,
                },
            ]
        }
    )


def _claude_initialize() -> str:
    return "\n".join(
        (
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "response": {
                            "account": {
                                "email": "private@example.com",
                                "organization": "private-org",
                            },
                            "models": [
                                {
                                    "value": "default",
                                    "resolvedModel": "claude-opus-5[1m]",
                                    "supportsEffort": True,
                                    "supportedEffortLevels": [
                                        "low",
                                        "medium",
                                        "high",
                                        "xhigh",
                                        "max",
                                    ],
                                },
                                {
                                    "value": "haiku",
                                    "resolvedModel": "claude-haiku-4-5",
                                    "supportsEffort": False,
                                    "supportedEffortLevels": None,
                                },
                            ],
                        },
                    },
                }
            ),
        )
    )


def test_codex_catalog_keeps_per_model_efforts_and_future_values() -> None:
    payload = json.loads(_codex_catalog())
    payload["models"][1]["supported_reasoning_levels"].append(
        {"effort": "future-effort"}
    )

    snapshot = parse_codex_models(
        json.dumps(payload),
        cli_version="codex-cli test",
    )

    assert snapshot.default_model == "gpt-5.6-sol"
    assert [model.model for model in snapshot.models] == [
        "gpt-5.6-sol",
        "gpt-5.5",
    ]
    assert snapshot.models[0].supported_efforts[-1] == "future-effort"
    assert snapshot.models[1].supported_efforts[-1] == "xhigh"


def test_codex_unsupported_effort_uses_same_model_advertised_maximum() -> None:
    snapshot = parse_codex_models(_codex_catalog(), cli_version="codex-cli test")

    selection = select_runtime_configuration(
        snapshot,
        requested_model="gpt-5.5",
        requested_effort="ultra",
    )

    assert selection.model == "gpt-5.5"
    assert selection.effort == "xhigh"
    assert "used advertised maximum" in (selection.fallback_reason or "")


def test_codex_exact_supported_pair_does_not_fallback() -> None:
    snapshot = parse_codex_models(_codex_catalog(), cli_version="codex-cli test")

    selection = select_runtime_configuration(
        snapshot,
        requested_model="gpt-5.6-sol",
        requested_effort="ultra",
    )

    assert selection.model == "gpt-5.6-sol"
    assert selection.effort == "ultra"
    assert selection.fallback_reason is None


def test_bounded_codex_work_uses_configured_model_at_live_maximum() -> None:
    snapshot = parse_codex_models(_codex_catalog(), cli_version="codex-cli test")

    selection = select_codex_task_configuration(
        snapshot,
        stage="implement",
        risk="low",
        design_required=False,
        bounded_model="gpt-5.5",
        requested_effort=None,
    )

    assert selection.model == "gpt-5.5"
    assert selection.effort == "xhigh"
    assert selection.policy_reason == "bounded implementation Work policy"


@pytest.mark.parametrize(
    ("stage", "risk", "design_required"),
    [
        ("implement", "high", False),
        ("implement", "low", True),
        ("repair", "low", False),
    ],
)
def test_complex_codex_work_uses_live_provider_default_at_maximum(
    stage: str,
    risk: str,
    design_required: bool,
) -> None:
    snapshot = parse_codex_models(_codex_catalog(), cli_version="codex-cli test")

    selection = select_codex_task_configuration(
        snapshot,
        stage=stage,
        risk=risk,
        design_required=design_required,
        bounded_model="gpt-5.5",
        requested_effort=None,
    )

    assert selection.model == "gpt-5.6-sol"
    assert selection.effort == "ultra"
    assert selection.policy_reason == "complex or repair Work policy"


@pytest.mark.parametrize(
    (
        "stage",
        "risk",
        "design_required",
        "expected_model",
        "expected_effort",
    ),
    [
        ("implement", "low", False, "gpt-5.5", "xhigh"),
        ("implement", "high", True, "gpt-5.6-sol", "ultra"),
        ("repair", "low", False, "gpt-5.6-sol", "ultra"),
    ],
)
def test_codex_work_policy_overrides_supported_lower_effort(
    stage: str,
    risk: str,
    design_required: bool,
    expected_model: str,
    expected_effort: str,
) -> None:
    snapshot = parse_codex_models(_codex_catalog(), cli_version="codex-cli test")

    selection = select_codex_task_configuration(
        snapshot,
        stage=stage,
        risk=risk,
        design_required=design_required,
        bounded_model="gpt-5.5",
        requested_effort="low",
    )

    assert selection.model == expected_model
    assert selection.effort == expected_effort
    assert "overridden by Work policy maximum" in (
        selection.fallback_reason or ""
    )


@pytest.mark.asyncio
async def test_concurrent_codex_tasks_keep_selection_evidence_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = parse_codex_models(_codex_catalog(), cli_version="codex-cli test")
    runtime = RefreshingCodexRuntime(
        snapshot=snapshot,
        requested_model="gpt-5.5",
        requested_effort=None,
    )
    observations: list[tuple[str, str, str]] = []

    async def run_selected(
        selected_runtime,
        request,
        _capsule,
        _capabilities,
        _workspace,
    ):
        model = selected_runtime._model
        await asyncio.sleep(0.01)
        note = selected_runtime._selection_note
        observations.append((request.run_id, model, note))
        return note

    monkeypatch.setattr(
        "sagewai.work.runtime_capabilities.CodexRuntime.run",
        run_selected,
    )
    bounded_request = SimpleNamespace(run_id="bounded", stage="implement")
    bounded_capsule = SimpleNamespace(
        contract=SimpleNamespace(risk="low", design_required=False)
    )
    complex_request = SimpleNamespace(run_id="complex", stage="repair")
    complex_capsule = SimpleNamespace(
        contract=SimpleNamespace(risk="low", design_required=False)
    )

    bounded_note, complex_note = await asyncio.gather(
        runtime.run(bounded_request, bounded_capsule, None, None),
        runtime.run(complex_request, complex_capsule, None, None),
    )

    assert "model=gpt-5.5, effort=xhigh" in bounded_note
    assert "model=gpt-5.6-sol, effort=ultra" in complex_note
    assert all(f"model={model}" in note for _, model, note in observations)


def test_unavailable_model_uses_live_provider_default() -> None:
    snapshot = parse_codex_models(_codex_catalog(), cli_version="codex-cli test")

    selection = select_runtime_configuration(
        snapshot,
        requested_model="retired-model",
        requested_effort="ultra",
    )

    assert selection.model == "gpt-5.6-sol"
    assert selection.effort == "ultra"
    assert "used provider default" in (selection.fallback_reason or "")


def test_claude_catalog_discards_account_data_and_matches_resolved_alias() -> None:
    snapshot = parse_claude_initialize(
        _claude_initialize(),
        cli_version="Claude Code test",
    )

    selection = select_runtime_configuration(
        snapshot,
        requested_model="claude-opus-5",
        requested_effort="max",
    )

    assert selection.model == "claude-opus-5"
    assert selection.effort == "max"
    assert selection.fallback_reason is None
    assert "private@example.com" not in snapshot.model_dump_json()
    assert "private-org" not in snapshot.model_dump_json()


def test_claude_model_without_effort_falls_back_to_default_maximum() -> None:
    snapshot = parse_claude_initialize(
        _claude_initialize(),
        cli_version="Claude Code test",
    )

    selection = select_runtime_configuration(
        snapshot,
        requested_model="haiku",
        requested_effort="high",
    )

    assert selection.model == "default"
    assert selection.effort == "max"
    assert "provider default maximum" in (selection.fallback_reason or "")


@pytest.mark.parametrize("payload", ["", "{}", '{"models": []}'])
def test_invalid_codex_catalog_fails_closed(payload: str) -> None:
    with pytest.raises(RuntimeCapabilityProbeError):
        parse_codex_models(payload, cli_version="codex-cli test")


@pytest.mark.asyncio
async def test_long_running_codex_runtime_refreshes_changed_model_efforts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = parse_codex_models(_codex_catalog(), cli_version="codex-cli old")
    updated_payload = json.loads(_codex_catalog())
    updated_payload["models"][0]["supported_reasoning_levels"].append(
        {"effort": "ultra"}
    )
    updated = parse_codex_models(
        json.dumps(updated_payload),
        cli_version="codex-cli new",
    )
    runtime = RefreshingCodexRuntime(
        snapshot=initial,
        requested_model="gpt-5.5",
        requested_effort="ultra",
        refresh_interval_seconds=1,
    )
    calls = 0

    async def probe(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return updated

    monkeypatch.setattr(
        "sagewai.work.runtime_capabilities.probe_runtime_capabilities",
        probe,
    )
    runtime._last_probe = 0

    await runtime._refresh_if_due()
    runtime._select_for_task(
        SimpleNamespace(stage="implement"),
        SimpleNamespace(
            contract=SimpleNamespace(risk="low", design_required=False)
        ),
    )

    assert calls == 1
    assert runtime._model == "gpt-5.5"
    assert runtime._reasoning_effort == "ultra"
    assert "unsupported" not in (runtime._selection_note or "")
