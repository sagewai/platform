# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""What the composer returns and what the report profile persists (spec section 12)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from sagewai.work.contract import WorkContract
from sagewai.work.tasks.models import Sink


class ComposedSource(BaseModel):
    """One source the composer fetched and wrote into its scratch workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)
    path: str = Field(min_length=1)
    fetched_at: datetime


class ComposedClaim(BaseModel):
    """One statement in the report and the source URLs that support it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str = Field(min_length=1)
    source_urls: tuple[str, ...] = Field(min_length=1)


class ReportResult(BaseModel):
    """``report_result`` in the composer's profile_context (spec section 12 step 1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str = Field(min_length=1)
    report_path: str = "report.md"
    sources_used: tuple[ComposedSource, ...] = ()
    claims: tuple[ComposedClaim, ...] = ()


class SourceSnapshot(BaseModel):
    """One fetched source persisted as an artifact with its URL, time, and content hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_ref: str
    url: str
    fetched_at: datetime
    content_sha256: str
    size_bytes: int


class ReportClaim(BaseModel):
    """A claim resolved against the snapshots that back it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str
    snapshot_refs: tuple[str, ...]


class ReportArchive(BaseModel):
    """The redacted report and its snapshots, as stored on the Work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_ref: str
    report_bytes: int
    report_sha256: str
    snapshots: tuple[SourceSnapshot, ...] = ()
    claims: tuple[ReportClaim, ...] = ()


class ReportContractContext(BaseModel):
    """Report-specific immutable contract state, mirroring SoftwareContractContext."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str | None
    task_id: str
    cycle: int
    report_criterion_id: str
    required_sections: tuple[str, ...] = ()
    max_bytes: int = Field(default=200_000, ge=1)
    allowed_hosts: tuple[str, ...] = ()
    sinks: tuple[Sink, ...] = ()

    def validate_contract(self, contract: WorkContract) -> None:
        if self.project_id != contract.project_id:
            raise ValueError("report context belongs to a different project")
        if self.report_criterion_id not in {c.id for c in contract.acceptance_criteria}:
            raise ValueError("report criterion is not in the accepted contract")
