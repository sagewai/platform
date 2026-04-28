from sagewai.sealed.replay import (
    InjectionSnapshot,
    LegacyRunNoSnapshotError,
    ModeNotReplayableError,
    ReplayError,
    RotationDriftError,
    WorkflowVersionMismatchError,
)


def test_injection_snapshot_roundtrip_via_pydantic():
    snap = InjectionSnapshot(
        effective_env_keys=["A", "B"],
        effective_secret_keys=["A"],
        security_profile_ref="builtin://acme",
        secret_value_hashes={"A": "abc"},
        secret_value_versions={"A": None},
        revocations_active_at_step={},
        captured_at=1234.5,
    )
    assert InjectionSnapshot.model_validate(snap.model_dump()) == snap


def test_replay_errors_subclass_replay_error():
    assert issubclass(LegacyRunNoSnapshotError, ReplayError)
    assert issubclass(WorkflowVersionMismatchError, ReplayError)
    assert issubclass(RotationDriftError, ReplayError)
    assert issubclass(ModeNotReplayableError, ReplayError)


def test_rotation_drift_error_message_includes_profile_and_key():
    err = RotationDriftError(profile_id="acme", secret_key="OPENAI_API_KEY")
    assert "acme" in str(err) and "OPENAI_API_KEY" in str(err)


def test_legacy_error_message_includes_run_and_step():
    err = LegacyRunNoSnapshotError(run_id="r-1", step_name="scaffold")
    assert "r-1" in str(err) and "scaffold" in str(err)
