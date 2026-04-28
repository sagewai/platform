from sagewai.core.state import DurableWorkflow
from sagewai.sealed.replay import compute_code_hash


async def _noop(x: str) -> str:
    return x


def _wf(step_names: list[str]) -> DurableWorkflow:
    wf = DurableWorkflow(name="t")
    for n in step_names:
        wf.step(n)(_noop)
    return wf


def test_code_hash_stable_for_same_step_list():
    assert compute_code_hash(_wf(["a", "b"])) == compute_code_hash(_wf(["a", "b"]))


def test_code_hash_changes_when_step_added():
    assert compute_code_hash(_wf(["a"])) != compute_code_hash(_wf(["a", "b"]))


def test_code_hash_changes_when_step_renamed():
    assert compute_code_hash(_wf(["a"])) != compute_code_hash(_wf(["A"]))


def test_code_hash_changes_when_step_reordered():
    assert compute_code_hash(_wf(["a", "b"])) != compute_code_hash(_wf(["b", "a"]))


def test_code_hash_is_hex_sha256():
    h = compute_code_hash(_wf(["a"]))
    assert len(h) == 64
    int(h, 16)  # valid hex
