# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Public surface of sagewai.work.tasks."""

from __future__ import annotations


def test_package_exports_kernel_names() -> None:
    import sagewai.work.tasks as tasks

    for name in (
        "Task", "TaskRecord", "TaskDefaults", "TaskKind", "TaskOrigin", "TaskStatus",
        "TaskEvent", "TaskEventType", "fold_record", "board_column", "assert_transition",
        "TaskStore", "StaleTaskError", "FeedBus", "FeedEntry", "next_fire", "validate_cron",
    ):
        assert hasattr(tasks, name), name


def test_package_exports_planning_names() -> None:
    import sagewai.work.tasks as tasks

    for name in (
        "CATALOGUE", "TaskTemplate", "SlotSpec", "validate_slots", "get_template",
        "IntakeResult", "ClarificationQuestion", "route",
        "TaskPlanResult", "PlanStep", "MatrixItem", "AcceptedPlan", "PlanRejectedError", "accept_plan",
        "ScratchWorkspace", "ScratchWorkspaceManager", "ScratchResultValidator",
        "TaskPlanner", "PlanningFailedError",
    ):
        assert hasattr(tasks, name), name


def test_package_exports_task_service_names_and_sorted_all() -> None:
    import sagewai.work.tasks as tasks

    for name in (
        "ClarificationDeadlines",
        "TaskCreationError",
        "TaskDecisionError",
        "TaskService",
        "TaskWriter",
        "plan_from_events",
        "status_entry",
    ):
        assert hasattr(tasks, name), name
    assert tasks.__all__ == sorted(tasks.__all__)
