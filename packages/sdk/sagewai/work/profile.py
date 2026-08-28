# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Minimal domain-profile boundary for generic Work execution."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sagewai.work.contract import WorkContract
from sagewai.work.models import (
    ActionPlan,
    ActionResult,
    VerificationResult,
    WorkItem,
)


@runtime_checkable
class WorkProfile(Protocol):
    """Supply domain actions and verification semantics to the Work kernel."""

    name: str

    async def prepare(
        self,
        work: WorkItem,
        contract: WorkContract,
    ) -> ActionPlan: ...

    async def verify(
        self,
        work: WorkItem,
        actions: tuple[ActionResult, ...],
    ) -> VerificationResult: ...
