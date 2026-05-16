# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Verify the public API surface of sagewai.autopilot.curator."""

from __future__ import annotations


def test_curator_importable():
    from sagewai.autopilot.curator import Curator

    assert callable(Curator)


def test_promoter_importable():
    from sagewai.autopilot.curator import Promoter

    assert callable(Promoter)


def test_training_dataset_importable():
    from sagewai.autopilot.curator import TrainingDataset

    assert callable(TrainingDataset)


def test_fine_tune_job_importable():
    from sagewai.autopilot.curator import FineTuneJob

    assert callable(FineTuneJob)


def test_promotion_result_importable():
    from sagewai.autopilot.curator import PromotionResult

    assert callable(PromotionResult)


def test_curator_config_importable():
    from sagewai.autopilot.curator import CuratorConfig

    assert callable(CuratorConfig)


def test_all_exports_via_star():
    import sagewai.autopilot.curator as pkg

    for name in (
        "Curator",
        "Promoter",
        "TrainingDataset",
        "FineTuneJob",
        "PromotionResult",
        "CuratorConfig",
    ):
        assert hasattr(pkg, name), f"{name!r} missing from sagewai.autopilot.curator"


def test_top_level_curator_importable():
    from sagewai.autopilot import Curator

    assert callable(Curator)


def test_top_level_promoter_importable():
    from sagewai.autopilot import Promoter

    assert callable(Promoter)


def test_top_level_training_dataset_importable():
    from sagewai.autopilot import TrainingDataset

    assert callable(TrainingDataset)


def test_top_level_fine_tune_job_importable():
    from sagewai.autopilot import FineTuneJob

    assert callable(FineTuneJob)


def test_top_level_promotion_result_importable():
    from sagewai.autopilot import PromotionResult

    assert callable(PromotionResult)


def test_top_level_curator_config_importable():
    from sagewai.autopilot import CuratorConfig

    assert callable(CuratorConfig)
