"""Audit every SDK example for explicit sandbox requirements.

Every ``.enqueue()`` or ``.enqueue_workflow()`` call site must declare
all three sandbox requirements.  The check handles two call shapes:

* **Keyword-arg form** (DurableWorkflow / store) — the three names appear
  as keyword arguments::

      await wf.enqueue(
          input_data=payload,
          requires_sandbox_mode=SandboxMode.PER_RUN,
          requires_image=f"ghcr.io/sagewai/sandbox-ml:{image_manifest.SDK_VERSION}",
          requires_network_policy=NetworkPolicy.FULL,
      )

* **Dict-literal form** (InMemoryTaskStore fleet dispatch) — the three
  names appear as string keys inside the first positional dict argument::

      task_store.enqueue({
          "run_id": ...,
          "requires_sandbox_mode": SandboxMode.PER_RUN,
          "requires_image": f"ghcr.io/sagewai/sandbox-base:...",
          "requires_network_policy": NetworkPolicy.NONE,
      })
"""
from __future__ import annotations

import ast
import pathlib

import pytest

EXAMPLES_DIR = pathlib.Path(__file__).parent.parent / "sagewai" / "examples"

REQUIRED_FIELDS = frozenset(
    {
        "requires_sandbox_mode",
        "requires_image",
        "requires_network_policy",
    }
)


def _enqueue_calls_in(file_path: pathlib.Path) -> list[ast.Call]:
    """Return every .enqueue() / .enqueue_workflow() AST Call node in *file_path*."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"enqueue", "enqueue_workflow"}
    ]


def _missing_from_call(call: ast.Call) -> set[str] | None:
    """Return any REQUIRED_FIELDS missing from *call*, or None to skip.

    Returns ``None`` when the call cannot be statically verified
    (e.g. a pre-built variable is passed as the first positional arg).

    Accepts two forms:
    1. Keyword-arg form: ``enqueue(requires_sandbox_mode=…, …)``
    2. Dict-literal form: ``enqueue({"requires_sandbox_mode": …, …})``
    """
    # --- keyword-arg form ---------------------------------------------------
    kwarg_names = {kw.arg for kw in call.keywords if kw.arg}
    if kwarg_names:
        # Keywords present — require all three.
        return REQUIRED_FIELDS - kwarg_names

    # --- dict-literal form (first positional arg) ---------------------------
    if call.args and isinstance(call.args[0], ast.Dict):
        dict_node: ast.Dict = call.args[0]
        string_keys: set[str] = set()
        for key in dict_node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                string_keys.add(key.value)
        # Return which required fields are missing from the dict.
        return REQUIRED_FIELDS - string_keys

    # First arg is a non-literal (variable, call, etc.) — cannot verify
    # statically.  Return None to let the caller skip this call site.
    return None


@pytest.mark.parametrize(
    "example_path",
    sorted(p for p in EXAMPLES_DIR.glob("*.py") if p.name != "__init__.py"),
    ids=lambda p: p.name,
)
def test_example_declares_sandbox_requirements(example_path: pathlib.Path) -> None:
    """Every .enqueue() call in an SDK example must declare all sandbox requirements."""
    calls = _enqueue_calls_in(example_path)
    if not calls:
        pytest.skip(f"No .enqueue() calls in {example_path.name}")

    errors: list[str] = []
    for call in calls:
        missing = _missing_from_call(call)
        if missing is None:
            # Non-literal first arg — cannot verify statically; skip.
            continue
        if missing:
            errors.append(
                f"  line {call.lineno}: .enqueue() missing {sorted(missing)}"
            )

    assert not errors, (
        f"{example_path.name} has enqueue() calls without explicit sandbox "
        f"requirements:\n"
        + "\n".join(errors)
        + "\n\n"
        "Add the three required fields (as kwargs or dict keys depending on "
        "the call form):\n"
        "  requires_sandbox_mode=SandboxMode.NONE,\n"
        '  requires_image=f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}",\n'
        "  requires_network_policy=NetworkPolicy.NONE,\n"
    )
