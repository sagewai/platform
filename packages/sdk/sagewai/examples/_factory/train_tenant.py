# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Per-tenant training flywheel for the dark-factory examples.

Each factory collects ``TrainingSample`` rows as it runs. At the end of
the example we export just that tenant's samples to Alpaca-style JSONL
and either:

* run a **stub** Unsloth pass (the default — zero GPU requirement, used
  in CI and local demos), or
* shell out to real Unsloth when ``FACTORIES_REAL_TRAINING=1`` is set.

Either path ends by registering a "trained" local tier in the tenant's
harness config so the factory can demo the cost delta before vs after
training on a synthetic batch.

The storage here is deliberately tiny and in-memory. A real deployment
would use the project-scoped tables exposed by the admin API
(``/api/v1/training/*``). Swapping the store out is a two-line change.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class TrainingSample:
    """One (prompt, completion) row tagged to a tenant."""

    tenant: str
    agent_name: str
    model: str
    input_text: str
    output_text: str
    quality: int = 0
    created_at: str = field(default_factory=_iso_now)

    def to_alpaca(self) -> dict[str, str]:
        return {
            "instruction": self.input_text,
            "input": "",
            "output": self.output_text,
        }


# In-memory, per-tenant store. Bootstrap resets this on each run.
_STORE: dict[str, list[TrainingSample]] = {}


def collect_sample(sample: TrainingSample) -> None:
    """Append a sample to its tenant's store."""
    _STORE.setdefault(sample.tenant, []).append(sample)


def samples_for(tenant: str) -> list[TrainingSample]:
    return list(_STORE.get(tenant, []))


def reset(tenant: str | None = None) -> None:
    """Clear the store for one tenant, or all of it."""
    if tenant is None:
        _STORE.clear()
    else:
        _STORE.pop(tenant, None)


def export_jsonl(
    tenant: str,
    *,
    output_dir: Path | str | None = None,
    min_quality: int = 0,
) -> Path:
    """Write the tenant's curated samples to Alpaca JSONL."""
    output_dir = Path(output_dir) if output_dir else (
        Path.home() / ".sagewai" / "training" / tenant
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        s for s in samples_for(tenant) if s.quality >= min_quality
    ]
    path = output_dir / f"{tenant}-alpaca.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.to_alpaca()) + "\n")
    return path


@dataclass
class TrainingResult:
    """What ``run_unsloth_stub`` / real Unsloth returns."""

    tenant: str
    base_model: str
    trained_tier_name: str
    samples_used: int
    jsonl_path: Path
    real: bool  # True when actual Unsloth was invoked


def run_unsloth_stub(
    tenant: str,
    *,
    base_model: str,
    output_dir: Path | str | None = None,
    real: bool | None = None,
) -> TrainingResult:
    """Either fake a training run (CI path) or call real Unsloth.

    The stub path is a literal no-op: it exports the JSONL, invents a
    new harness tier name, and returns. Later code paths register that
    tier in the tenant's harness so routing visibly shifts.

    The real path (``FACTORIES_REAL_TRAINING=1`` or ``real=True``) shells
    out to ``unsloth.train`` — but only when explicitly requested, since
    it needs a GPU, Python deps, and 10+ minutes.
    """
    if real is None:
        real = os.environ.get("FACTORIES_REAL_TRAINING", "") == "1"

    jsonl_path = export_jsonl(tenant, output_dir=output_dir)
    sample_count = len(samples_for(tenant))

    trained_tier = f"{base_model}-trained-{tenant}"

    if real:
        # Intentionally unreachable in CI. The real integration lives in
        # example 17; this wrapper just documents the handshake.
        raise NotImplementedError(
            "Real Unsloth training isn't wired from the factory helper "
            "yet — run examples/17_unsloth_finetune.py manually and "
            "then call register_trained_tier() with the resulting tag."
        )

    return TrainingResult(
        tenant=tenant,
        base_model=base_model,
        trained_tier_name=trained_tier,
        samples_used=sample_count,
        jsonl_path=jsonl_path,
        real=False,
    )


def register_trained_tier(
    tenant: str,
    *,
    trained_tier_name: str,
    harness_config: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Prepend a trained tier to the tenant's harness model list.

    ``harness_config`` is a simple ``{tenant: [model, model, ...]}`` dict
    passed in by the factory example — we don't want the helper to take
    a hard dependency on the full ``HarnessKey`` / ``ModelTierConfig``
    machinery just for the demo. Example scripts that do use the real
    harness store can ignore this helper entirely.
    """
    current = list(harness_config.get(tenant, []))
    if trained_tier_name not in current:
        current.insert(0, trained_tier_name)
    harness_config[tenant] = current
    return harness_config
