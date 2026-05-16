# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Plan ART — workflow_runs.artifact_destination column.

Spec:        docs/superpowers/specs/2026-04-27-plan-art-artifact-destination-design.md
Driving plan: docs/superpowers/plans/2026-04-27-plan-art-artifact-destination.md

Summary
-------
Adds a JSONB ``artifact_destination`` column to ``workflow_runs`` that
captures the resolved destination (type, target, env_keys, options) at
enqueue time. NULL = no upload (also covers all Mode 0/1/2 runs and
Mode 3 'none' runs).

Resolved destinations are also persisted in the ``data`` JSONB blob
via ``WorkflowRun.to_dict()``. The typed column buys queryability —
"find all runs that pushed to a github destination" — without needing
to materialise every run.

Revision ID: 006_artifact_destination
Revises: 005_execution_mode
Create Date: 2026-04-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_artifact_destination"
down_revision: str | None = "005_execution_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("artifact_destination", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "artifact_destination")
