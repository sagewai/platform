# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Merge parallel 006 migration heads (artifact_destination + replay_snapshots).

No schema changes — pure Alembic-graph merge. Resolves the two-head
state created by PR #171 and PR #189 both chaining off 005_execution_mode.
"""
from collections.abc import Sequence

revision: str = "007_merge_006_branches"
# Tuple of two parents — Alembic merge migration form.
down_revision: tuple[str, str] = ("006_artifact_destination", "006_replay_snapshots")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
