# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""One-command bootstrap for the dark-factory multi-tenant demo.

Running this module creates the admin state for a fresh Sagewai
instance and provisions the four factory tenants — all four projects,
their budgets, default models, and a shared fleet of local runners.

Usage::

    # From the repo root — idempotent, safe to re-run.
    python -m sagewai.examples._factory.bootstrap_tenants

    # Use a custom state file (e.g. for tests):
    python -m sagewai.examples._factory.bootstrap_tenants --state /tmp/state.json

    # Skip the fleet (state file only):
    python -m sagewai.examples._factory.bootstrap_tenants --no-fleet

The bootstrap is also importable — see :func:`bootstrap` and
:func:`seed_fleet`. Factory examples 27–30 call those directly instead
of shelling out.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sagewai.admin.state_file import AdminStateFile
from sagewai.examples._factory.local_models import (
    LOCAL_MODEL_REGISTRY,
    ollama_preflight,
)

if TYPE_CHECKING:
    from sagewai.fleet.registry import FleetRegistry


# ── Tenant catalogue ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TenantSpec:
    """Static definition of one factory tenant.

    The bootstrap reads this and calls :meth:`AdminStateFile.create_project`
    plus :meth:`AdminStateFile.update_project` to persist it.
    """

    slug: str
    name: str
    description: str
    default_model: str
    daily_budget_usd: float
    dedicated_pool: str
    worker_count: int = 1
    dedicated_labels: dict[str, str] = field(default_factory=dict)


TENANTS: tuple[TenantSpec, ...] = (
    TenantSpec(
        slug="app-factory",
        name="App Factory",
        description="Idea-to-artifact code factory — app ideas land in Slack and ship as repos.",
        default_model="qwen2.5-coder:7b",
        daily_budget_usd=25.0,
        dedicated_pool="build",
        worker_count=1,
        dedicated_labels={"code": "true", "stack": "polyglot"},
    ),
    TenantSpec(
        slug="biz-ops",
        name="SMB Back Office",
        description="Odoo + socials + inventory + sales, always-on.",
        default_model="llama3.1:8b",
        daily_budget_usd=10.0,
        dedicated_pool="ops",
        worker_count=1,
        dedicated_labels={"odoo": "true", "social": "true"},
    ),
    TenantSpec(
        slug="wealth-desk",
        name="Wealth Desk",
        description="Weekly market read, rebalance proposals, bet alternatives.",
        default_model="qwen2.5:14b",
        daily_budget_usd=15.0,
        dedicated_pool="analysis",
        worker_count=1,
        dedicated_labels={"debate": "true", "risk": "true"},
    ),
    TenantSpec(
        slug="school-mentor",
        name="School Mentor",
        description="Per-student mentoring fleet with continuous career coaching.",
        default_model="llama3.2:3b",
        daily_budget_usd=5.0,
        dedicated_pool="mentor",
        worker_count=3,
        dedicated_labels={"mentor": "true"},
    ),
)


# ── Admin state bootstrap ────────────────────────────────────────────


_ADMIN_ORG_NAME = "Sagewai Factory Demo"
_ADMIN_ORG_SLUG = "factory-demo"
_ADMIN_EMAIL = "factory-admin@example.com"
_ADMIN_PASSWORD = "factory-demo-password"  # noqa: S105 — demo only


def _ensure_setup(state: AdminStateFile) -> None:
    """Run the first-time setup wizard if the state file is blank."""
    if state.is_setup_complete():
        return
    state.complete_setup(
        org_name=_ADMIN_ORG_NAME,
        org_slug=_ADMIN_ORG_SLUG,
        contact_email=_ADMIN_EMAIL,
        timezone="UTC",
        app_name="Factory Demo",
        app_description=(
            "Four dark-factory tenants sharing one Sagewai instance."
        ),
        admin_name="Factory Admin",
        admin_email=_ADMIN_EMAIL,
        admin_password=_ADMIN_PASSWORD,
    )


def ensure_project(
    state: AdminStateFile, spec: TenantSpec
) -> dict[str, object]:
    """Idempotently create or update one tenant project."""
    existing = {p["slug"]: p for p in state.list_projects()}
    if spec.slug not in existing:
        state.create_project(
            name=spec.name,
            slug=spec.slug,
            environment="production",
        )
    updated = state.update_project(
        spec.slug,
        {
            "name": spec.name,
            "default_model": spec.default_model,
            "status": "active",
        },
    )
    return updated or existing.get(spec.slug, {})


def bootstrap(
    state_path: Path | str | None = None,
    *,
    tenants: tuple[TenantSpec, ...] = TENANTS,
    run_preflight: bool = True,
) -> dict[str, object]:
    """Provision the full multi-tenant demo in admin state.

    Idempotent: safe to re-run. Returns a small report dict that the CLI
    prints and the test suite asserts against.
    """
    state = AdminStateFile(state_path)
    _ensure_setup(state)

    projects: list[dict[str, object]] = []
    for spec in tenants:
        projects.append(ensure_project(state, spec))

    preflight = None
    if run_preflight:
        preflight = ollama_preflight([t.slug for t in tenants])

    return {
        "state_path": str(state._path),  # noqa: SLF001 — internal by design
        "org": state.get_org(),
        "projects": projects,
        "preflight": preflight,
        "tenants": [t.slug for t in tenants],
    }


# ── Fleet seeding ────────────────────────────────────────────────────


def _build_capabilities(
    spec: TenantSpec,
    *,
    worker_index: int = 0,
):
    """Build a WorkerCapabilities for one dedicated worker of ``spec``."""
    from sagewai.fleet.models import WorkerCapabilities

    labels = dict(spec.dedicated_labels)
    labels["project_id"] = spec.slug
    if spec.worker_count > 1:
        labels["worker_index"] = str(worker_index)

    models = [tier.name for tier in LOCAL_MODEL_REGISTRY.get(spec.slug, ())]

    return WorkerCapabilities(
        models_supported=models,
        pool=spec.dedicated_pool,
        labels=labels,
        max_concurrent=2,
        sdk_version="factory-demo",
    )


def _build_overflow_capabilities():
    """Single shared overflow worker — no project_id, broad model list."""
    from sagewai.fleet.models import WorkerCapabilities

    seen: list[str] = []
    for tiers in LOCAL_MODEL_REGISTRY.values():
        for tier in tiers:
            if tier.name not in seen:
                seen.append(tier.name)

    return WorkerCapabilities(
        models_supported=seen,
        pool="default",
        labels={"overflow": "true"},
        max_concurrent=4,
        sdk_version="factory-demo",
    )


async def seed_fleet(
    registry: "FleetRegistry",
    *,
    tenants: tuple[TenantSpec, ...] = TENANTS,
    org_id: str = _ADMIN_ORG_SLUG,
) -> dict[str, list[str]]:
    """Register dedicated + overflow workers with a FleetRegistry.

    Returns a mapping of tenant slug → worker names, plus an ``__overflow__``
    key for shared runners. Uses an auto-approving enrollment key so the
    workers come back APPROVED — that keeps the demo noise-free.
    """
    key_record, raw_key = await registry.create_enrollment_key(
        org_id=org_id,
        name="factory-demo-enrollment",
        created_by="factory-bootstrap",
    )
    del key_record  # only the raw key is used

    report: dict[str, list[str]] = {"__overflow__": []}

    for spec in tenants:
        report[spec.slug] = []
        for idx in range(spec.worker_count):
            suffix = f"-{idx}" if spec.worker_count > 1 else ""
            name = f"{spec.slug}-{spec.dedicated_pool}{suffix}"
            worker = await registry.register_worker(
                name=name,
                org_id=org_id,
                capabilities=_build_capabilities(spec, worker_index=idx),
                enrollment_key=raw_key,
            )
            report[spec.slug].append(worker.name)

    overflow = await registry.register_worker(
        name="overflow-default",
        org_id=org_id,
        capabilities=_build_overflow_capabilities(),
        enrollment_key=raw_key,
    )
    report["__overflow__"].append(overflow.name)

    return report


# ── CLI ──────────────────────────────────────────────────────────────


def _render_report(report: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("Sagewai dark-factory bootstrap")
    lines.append("=" * 60)
    lines.append(f"state file : {report['state_path']}")
    org = report["org"]
    if isinstance(org, dict):
        lines.append(
            f"org        : {org.get('org_name')} <{org.get('admin_email')}>"
        )

    lines.append("\nTenants:")
    for spec in TENANTS:
        lines.append(
            f"  - {spec.slug:<14} default={spec.default_model:<20} "
            f"budget=${spec.daily_budget_usd:>5.0f}/d  "
            f"pool={spec.dedicated_pool}"
        )

    preflight = report.get("preflight")
    if preflight is not None:
        lines.append("")
        lines.append(preflight.render())
        if not preflight.ok:
            lines.append("")
            lines.append(
                "Preflight failed. Install Ollama (https://ollama.com)"
                " and run the pull commands above, then re-run this "
                "bootstrap. Set FACTORIES_ALLOW_CLOUD=1 to skip."
            )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bootstrap_tenants",
        description="Provision the four dark-factory tenants.",
    )
    p.add_argument(
        "--state",
        default=None,
        help="Path to admin state file (defaults to ~/.sagewai/admin-state.json).",
    )
    p.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip the Ollama preflight check.",
    )
    p.add_argument(
        "--no-fleet",
        action="store_true",
        help="Skip fleet seeding (just provision admin state).",
    )
    return p.parse_args(argv)


def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = bootstrap(
        state_path=args.state,
        run_preflight=not args.no_preflight,
    )
    print(_render_report(report))

    if not args.no_fleet:
        from sagewai.fleet.registry import InMemoryFleetRegistry

        registry = InMemoryFleetRegistry()
        fleet_report = asyncio.run(seed_fleet(registry))
        print("\nFleet seeded (in-memory registry):")
        for tenant, names in fleet_report.items():
            label = "overflow" if tenant == "__overflow__" else tenant
            for name in names:
                print(f"  {label:<14} → {name}")

    preflight = report.get("preflight")
    if preflight is not None and not preflight.ok:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
