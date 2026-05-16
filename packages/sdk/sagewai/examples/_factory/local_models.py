# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Local-first model registry for the dark-factory examples.

Every factory tenant ships with a mix of local Ollama models, chosen to
showcase per-tenant specialisation through the Sagewai harness:

* ``app-factory`` → ``qwen2.5-coder:7b`` — tool use + code synthesis
* ``biz-ops`` → ``llama3.1:8b`` — solid generalist for ops + social
* ``wealth-desk`` → ``qwen2.5:14b`` — strongest local reasoner for debate
* ``school-mentor`` → ``llama3.2:3b`` — fast, chatty, per-student workers

``ollama_preflight()`` probes ``ollama list`` and returns a report so the
bootstrap can print exact ``ollama pull`` commands for anything missing.
Set ``FACTORIES_ALLOW_CLOUD=1`` to relax the preflight — the factories
still prefer local tiers, but the harness is allowed to fall back to
cloud models when no suitable worker is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelTier:
    """One routing tier inside a tenant's harness config.

    Attributes
    ----------
    name:
        Canonical Ollama tag, e.g. ``llama3.1:8b``.
    role:
        Short human-readable label printed in the scoreboard.
    use_cases:
        Tasks this tier is best at. Used by the harness classifier.
    """

    name: str
    role: str
    use_cases: tuple[str, ...] = field(default_factory=tuple)


LOCAL_MODEL_REGISTRY: dict[str, tuple[ModelTier, ...]] = {
    "app-factory": (
        ModelTier(
            name="qwen2.5-coder:7b",
            role="default",
            use_cases=("code-synthesis", "tool-use", "refactor"),
        ),
        ModelTier(
            name="llama3.1:8b",
            role="fallback-generalist",
            use_cases=("research", "summarisation"),
        ),
    ),
    "biz-ops": (
        ModelTier(
            name="llama3.1:8b",
            role="default",
            use_cases=("ops", "copywriting", "classification"),
        ),
        ModelTier(
            name="llama3.2:3b",
            role="cheap-triage",
            use_cases=("triage", "routing", "short-reply"),
        ),
    ),
    "wealth-desk": (
        ModelTier(
            name="qwen2.5:14b",
            role="default",
            use_cases=("reasoning", "debate", "risk-analysis"),
        ),
        ModelTier(
            name="llama3.1:8b",
            role="data-fetch",
            use_cases=("summarisation", "extraction"),
        ),
    ),
    "school-mentor": (
        ModelTier(
            name="llama3.2:3b",
            role="default",
            use_cases=("chat", "check-in", "tone-match"),
        ),
        ModelTier(
            name="llama3.1:8b",
            role="planning",
            use_cases=("plan", "long-horizon"),
        ),
    ),
}


def all_required_models(tenants: list[str] | None = None) -> list[str]:
    """Return the union of model tags required across tenants, deduped."""
    slugs = tenants or list(LOCAL_MODEL_REGISTRY)
    seen: list[str] = []
    for slug in slugs:
        for tier in LOCAL_MODEL_REGISTRY.get(slug, ()):
            if tier.name not in seen:
                seen.append(tier.name)
    return seen


@dataclass
class PreflightReport:
    """Result of an Ollama preflight probe."""

    ollama_installed: bool
    ollama_running: bool
    available: list[str]
    missing: list[str]
    allow_cloud: bool

    @property
    def ok(self) -> bool:
        """True when the preflight doesn't block factory startup."""
        if self.allow_cloud:
            return True
        return self.ollama_installed and self.ollama_running and not self.missing

    def render(self) -> str:
        """Pretty, human-readable summary for the bootstrap CLI."""
        lines = ["Ollama preflight:"]
        lines.append(
            f"  installed : {'yes' if self.ollama_installed else 'no'}"
        )
        lines.append(
            f"  running   : {'yes' if self.ollama_running else 'no'}"
        )
        lines.append(f"  available : {', '.join(self.available) or '<none>'}")
        if self.missing and not self.allow_cloud:
            lines.append("  missing   :")
            for model in self.missing:
                lines.append(f"    - {model}   ({pull_hint(model)})")
        elif self.missing and self.allow_cloud:
            lines.append(
                "  missing   : "
                + ", ".join(self.missing)
                + "  (cloud fallback enabled)"
            )
        else:
            lines.append("  missing   : <none>")
        return "\n".join(lines)


def pull_hint(model: str) -> str:
    """Exact command a user should run to pull a missing Ollama model."""
    return f"ollama pull {model}"


def ollama_preflight(
    tenants: list[str] | None = None,
    *,
    allow_cloud: bool | None = None,
) -> PreflightReport:
    """Probe the local Ollama install and report which models are ready.

    The probe is intentionally forgiving — it returns a report instead
    of raising, so the bootstrap script can print a helpful error and
    exit cleanly if something is missing.

    Parameters
    ----------
    tenants:
        Optional subset of tenant slugs to scope the check to. Defaults
        to all four factory tenants.
    allow_cloud:
        Force the cloud-fallback mode on/off. When ``None`` (default),
        the environment variable ``FACTORIES_ALLOW_CLOUD`` is consulted.
    """
    if allow_cloud is None:
        allow_cloud = os.environ.get("FACTORIES_ALLOW_CLOUD", "") == "1"

    required = all_required_models(tenants)

    ollama_bin = shutil.which("ollama")
    if ollama_bin is None:
        return PreflightReport(
            ollama_installed=False,
            ollama_running=False,
            available=[],
            missing=required,
            allow_cloud=allow_cloud,
        )

    try:
        result = subprocess.run(
            [ollama_bin, "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return PreflightReport(
            ollama_installed=True,
            ollama_running=False,
            available=[],
            missing=required,
            allow_cloud=allow_cloud,
        )

    running = result.returncode == 0
    available: list[str] = []
    if running:
        # First line is a header; subsequent lines are "<name> <id> <size>..."
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if parts:
                available.append(parts[0])

    missing = [m for m in required if m not in available]
    return PreflightReport(
        ollama_installed=True,
        ollama_running=running,
        available=available,
        missing=missing,
        allow_cloud=allow_cloud,
    )
