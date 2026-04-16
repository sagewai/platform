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
