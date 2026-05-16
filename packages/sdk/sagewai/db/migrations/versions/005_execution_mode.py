# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Mode-aware runner — workflow_runs.execution_mode column.

Spec:        docs/architecture/execution-modes.md (Mode 0/1/2/3/3b taxonomy)
Driving plan: refactor/mode-aware-runner
PR:          (this branch)

Summary
-------
Adds a first-class ``execution_mode`` column on ``workflow_runs`` that
captures the architecture's Mode 0/1/2/3/3b taxonomy:

- ``bare``      → Mode 0: inline on worker, no sandbox
- ``sandboxed`` → Mode 1: sandbox, no identity
- ``identity``  → Mode 2: sandbox + Sealed identity injected
- ``full``      → Mode 3: + CLI agent + artifact destination
- ``full_jit``  → Mode 3b: + bidirectional JIT credential callback

The pre-existing ``requires_sandbox_mode`` column (Plan 3a's
``SandboxMode`` enum: NONE / PER_TOOL / PER_RUN / PER_WORKER) is kept
for backward compat — it still drives worker capability matching.
At enqueue, ``requires_sandbox_mode`` is computed from
``execution_mode``: BARE → NONE; everything else → PER_RUN.

Per-step mode override is a follow-up — for now the run-level mode is
what the worker sees.

Revision ID: 005_execution_mode
Revises: 004_sealed_revocations
Create Date: 2026-04-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005_execution_mode"
down_revision: str | None = "004_sealed_revocations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "execution_mode",
            sa.Text(),
            nullable=False,
            server_default="bare",
        ),
    )
    op.create_check_constraint(
        "runs_execution_mode_valid",
        "workflow_runs",
        "execution_mode IN ('bare','sandboxed','identity','full','full_jit')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "runs_execution_mode_valid", "workflow_runs", type_="check"
    )
    op.drop_column("workflow_runs", "execution_mode")
