# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sandbox requirement columns + capability rank functions.

Spec:        Plan 3a (sandbox fleet routing + admin read-out)
Driving plan: Plan 3a Tasks 5-20
PR:          #148

Summary
-------
Adds the four sandbox-requirement columns on ``workflow_runs`` that the
fleet dispatcher uses to route runs to capable workers:
``requires_sandbox_mode``, ``requires_image``, ``requires_variant``,
``requires_network_policy``. Three CHECK constraints lock the enum
values; a partial index speeds claims of pending runs by capability.
Two SQL helper functions (``sandbox_mode_rank``, ``network_policy_rank``)
are created so the dispatcher can compare worker capability ≥ run
requirement in a single predicate.

Revision ID: 002_sandbox_requirements
Revises: 001_initial
Create Date: 2026-04-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002_sandbox_requirements"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "requires_sandbox_mode",
            sa.Text(),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "requires_image",
            sa.Text(),
            nullable=False,
            server_default="ghcr.io/sagewai/sandbox-base:0.0.0-dev",
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("requires_variant", sa.Text(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "requires_network_policy",
            sa.Text(),
            nullable=False,
            server_default="none",
        ),
    )

    op.create_check_constraint(
        "runs_requires_sandbox_mode_valid",
        "workflow_runs",
        "requires_sandbox_mode IN ('none','per_tool','per_run','per_worker')",
    )
    op.create_check_constraint(
        "runs_requires_network_policy_valid",
        "workflow_runs",
        "requires_network_policy IN ('none','egress_allowlist','full')",
    )
    op.create_check_constraint(
        "runs_requires_variant_valid",
        "workflow_runs",
        "requires_variant IS NULL OR requires_variant IN "
        "('base','general','ml','ops','erp','ecommerce','api')",
    )

    op.create_index(
        "idx_wf_runs_pending_sandbox",
        "workflow_runs",
        [
            "status",
            "requires_variant",
            "requires_sandbox_mode",
            "requires_network_policy",
        ],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.execute(
        """
        CREATE FUNCTION sandbox_mode_rank(m TEXT) RETURNS INT IMMUTABLE LANGUAGE SQL AS $$
            SELECT CASE m
                WHEN 'none'       THEN 0
                WHEN 'per_tool'   THEN 1
                WHEN 'per_run'    THEN 2
                WHEN 'per_worker' THEN 3
            END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION network_policy_rank(p TEXT) RETURNS INT IMMUTABLE LANGUAGE SQL AS $$
            SELECT CASE p
                WHEN 'none'              THEN 0
                WHEN 'egress_allowlist'  THEN 1
                WHEN 'full'              THEN 2
            END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS network_policy_rank(TEXT)")
    op.execute("DROP FUNCTION IF EXISTS sandbox_mode_rank(TEXT)")
    op.drop_index("idx_wf_runs_pending_sandbox", table_name="workflow_runs")
    op.drop_constraint("runs_requires_variant_valid", "workflow_runs", type_="check")
    op.drop_constraint(
        "runs_requires_network_policy_valid", "workflow_runs", type_="check"
    )
    op.drop_constraint(
        "runs_requires_sandbox_mode_valid", "workflow_runs", type_="check"
    )
    op.drop_column("workflow_runs", "requires_network_policy")
    op.drop_column("workflow_runs", "requires_variant")
    op.drop_column("workflow_runs", "requires_image")
    op.drop_column("workflow_runs", "requires_sandbox_mode")
