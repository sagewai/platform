# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Directive policy storage shape + cascade resolution.

See spec §5. Cascade: workflow > project > system, by `id`.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sagewai.sealed.directives.models import DirectivePolicy


class EvaluatorSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_signals_per_poll: int = Field(default=50, ge=1)
    audit_retention_days: int = Field(default=365, ge=1)
    approval_default_ttl_seconds: int = Field(default=3600, ge=60)


class DirectivesConfig(BaseModel):
    """Persisted under admin-state.json → directives:."""

    model_config = ConfigDict(frozen=True)

    system_policies: list[DirectivePolicy] = Field(default_factory=list)
    project_policies: dict[str, list[DirectivePolicy]] = Field(default_factory=dict)
    workflow_policies: dict[str, list[DirectivePolicy]] = Field(default_factory=dict)
    profile_suggestions: dict[str, str] = Field(default_factory=dict)
    evaluator_settings: EvaluatorSettings = Field(default_factory=EvaluatorSettings)


def default_alert_only_policies() -> list[DirectivePolicy]:
    """Out-of-the-box alert-only defaults — the 'observation grade' starting point."""
    from sagewai.sealed.directives.models import PolicyAction, PolicyCondition

    return [
        DirectivePolicy(
            id="cost-overrun-default",
            name="Alert on >5x cost",
            description="Default: alert when actual cost > 5× estimated.",
            condition=PolicyCondition(
                signal_kind="cost_overrun",
                severity_at_least="warning",
            ),
            action=PolicyAction(
                kind="alert_operator",
                severity="warning",
                message_template=(
                    "Run {run_id}: cost {evidence.actual_cost_usd} > "
                    "{evidence.multiplier}× estimate {evidence.estimated_cost_usd}"
                ),
            ),
        ),
        DirectivePolicy(
            id="capability-gap-default",
            name="Alert on credential gap",
            description="Default: alert when a step fails with a credential error.",
            condition=PolicyCondition(signal_kind="capability_gap"),
            action=PolicyAction(
                kind="alert_operator",
                severity="warning",
                message_template=(
                    "Step {evidence.step_index} ({evidence.step_name}): "
                    "missing key {evidence.missing_key}"
                ),
            ),
        ),
        DirectivePolicy(
            id="rotation-drift-default",
            name="Alert on identity rotation",
            description="Default: alert when a profile rotates mid-run.",
            condition=PolicyCondition(signal_kind="rotation_drift"),
            action=PolicyAction(
                kind="alert_operator",
                severity="info",
                message_template=(
                    "Run {run_id}: profile {evidence.profile_id} rotated mid-run"
                ),
            ),
        ),
    ]


def seed_defaults_if_empty(config: DirectivesConfig) -> DirectivesConfig:
    """Returns config unchanged if any system_policies exist; otherwise seeds defaults."""
    if config.system_policies:
        return config
    return config.model_copy(update={"system_policies": default_alert_only_policies()})


def resolve_directive_policies(
    *,
    workflow_name: str,
    project_id: str | None,
    config: DirectivesConfig,
) -> list[DirectivePolicy]:
    """Cascade: workflow > project > system. Same-id at deeper level overrides."""
    by_id: dict[str, DirectivePolicy] = {}
    for policy in config.system_policies:
        by_id[policy.id] = policy
    if project_id:
        for policy in config.project_policies.get(project_id, []):
            by_id[policy.id] = policy
    for policy in config.workflow_policies.get(workflow_name, []):
        by_id[policy.id] = policy
    return [p for p in by_id.values() if p.enabled]
