# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cascade resolution: workflow > project > system, by-id override."""
from __future__ import annotations

from sagewai.sealed.directives.models import (
    DirectivePolicy,
    PolicyAction,
    PolicyCondition,
)
from sagewai.sealed.directives.policies import (
    DirectivesConfig,
    resolve_directive_policies,
)


def _policy(id: str, message: str = "x") -> DirectivePolicy:
    return DirectivePolicy(
        id=id,
        name=id,
        condition=PolicyCondition(signal_kind="cost_overrun"),
        action=PolicyAction(kind="alert_operator", message_template=message),
    )


def test_system_only_policies_returned():
    cfg = DirectivesConfig(
        system_policies=[_policy("default")],
        project_policies={},
        workflow_policies={},
    )
    out = resolve_directive_policies(workflow_name="wf", project_id="p", config=cfg)
    assert [p.id for p in out] == ["default"]


def test_workflow_overrides_project_overrides_system_by_id():
    cfg = DirectivesConfig(
        system_policies=[_policy("default", "system")],
        project_policies={"p1": [_policy("default", "project")]},
        workflow_policies={"wf": [_policy("default", "workflow")]},
    )
    out = resolve_directive_policies(workflow_name="wf", project_id="p1", config=cfg)
    assert len(out) == 1
    assert out[0].action.message_template == "workflow"


def test_disabled_policies_filtered():
    cfg = DirectivesConfig(
        system_policies=[
            DirectivePolicy(
                id="off",
                name="off",
                enabled=False,
                condition=PolicyCondition(signal_kind="cost_overrun"),
                action=PolicyAction(kind="alert_operator"),
            )
        ],
        project_policies={},
        workflow_policies={},
    )
    out = resolve_directive_policies(workflow_name="wf", project_id=None, config=cfg)
    assert out == []


def test_workflow_adds_new_policy_id_additively():
    cfg = DirectivesConfig(
        system_policies=[_policy("a")],
        project_policies={},
        workflow_policies={"wf": [_policy("b")]},
    )
    out = resolve_directive_policies(workflow_name="wf", project_id=None, config=cfg)
    assert sorted(p.id for p in out) == ["a", "b"]
