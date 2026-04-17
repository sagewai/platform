# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai Autopilot curator — Layer 5 learning loop.

Public API surface for the curator subpackage. Callers import
:class:`Curator`, :class:`Promoter`, and the supporting types from here.
"""

from __future__ import annotations

from .curator import Curator
from .fine_tune import FineTuneConfig, FineTuneExecutor, FineTuneResult
from .promoter import Promoter
from .types import CuratorConfig, FineTuneJob, PromotionResult, TrainingDataset

__all__ = [
    "Curator",
    "FineTuneConfig",
    "FineTuneExecutor",
    "FineTuneResult",
    "Promoter",
    "TrainingDataset",
    "FineTuneJob",
    "PromotionResult",
    "CuratorConfig",
]
