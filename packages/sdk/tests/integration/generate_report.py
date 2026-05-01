# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Generate a validation report from integration test results.

Usage:
    cd packages/sagewai
    uv run python -m pytest tests/integration/ -v -m integration --tb=short --junitxml=validation-results.xml
    uv run python tests/integration/generate_report.py
"""

from __future__ import annotations

import datetime
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_junit_xml(path: str) -> list[dict]:
    """Parse JUnit XML test results."""
    tree = ET.parse(path)
    root = tree.getroot()

    results = []
    for suite in root.iter("testsuite"):
        for case in suite.iter("testcase"):
            result = {
                "name": case.get("name", ""),
                "classname": case.get("classname", ""),
                "time": float(case.get("time", 0)),
                "status": "passed",
                "error": "",
            }
            failure = case.find("failure")
            if failure is not None:
                result["status"] = "failed"
                result["error"] = failure.get("message", "")[:200]
            error = case.find("error")
            if error is not None:
                result["status"] = "error"
                result["error"] = error.get("message", "")[:200]
            skip = case.find("skipped")
            if skip is not None:
                result["status"] = "skipped"
                result["error"] = skip.get("message", "")[:200]
            results.append(result)
    return results


def generate_report(results: list[dict]) -> str:
    """Generate markdown validation report."""
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    errors = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skipped")

    lines = [
        "# Sagewai SDK — Real-World Validation Report",
        "",
        f"**Date**: {datetime.date.today()}",
        f"**Total Scenarios**: {total}",
        f"**Passed**: {passed} | **Failed**: {failed} | **Errors**: {errors} | **Skipped**: {skipped}",
        f"**Pass Rate**: {passed / max(total - skipped, 1) * 100:.1f}%",
        "",
        "## Results by Tier",
        "",
    ]

    # Group by tier (from classname)
    tiers: dict[str, list[dict]] = {}
    for r in results:
        tier = "Unknown"
        cn = r["classname"]
        if "tier1" in cn:
            tier = "Tier 1: Core Chat"
        elif "tier2" in cn:
            tier = "Tier 2: Strategies"
        elif "tier3" in cn:
            tier = "Tier 3: Orchestration"
        elif "tier4" in cn:
            tier = "Tier 4: Memory & RAG"
        elif "tier5" in cn:
            tier = "Tier 5: Safety & Auth"
        elif "tier6" in cn:
            tier = "Tier 6: MCP Protocol"
        elif "tier7" in cn:
            tier = "Tier 7: Model Comparison"
        tiers.setdefault(tier, []).append(r)

    for tier, cases in sorted(tiers.items()):
        tier_passed = sum(1 for c in cases if c["status"] == "passed")
        lines.append(f"### {tier} ({tier_passed}/{len(cases)} passed)")
        lines.append("")
        lines.append("| Test | Status | Time (s) | Error |")
        lines.append("|------|--------|----------|-------|")
        for c in cases:
            status_icon = {
                "passed": "PASS",
                "failed": "FAIL",
                "error": "ERR",
                "skipped": "SKIP",
            }
            lines.append(
                f"| {c['name'][:60]} "
                f"| {status_icon.get(c['status'], '?')} "
                f"| {c['time']:.2f} "
                f"| {c['error'][:80]} |"
            )
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    xml_path = "validation-results.xml"
    if not Path(xml_path).exists():
        print(
            f"Run pytest first: uv run python -m pytest tests/integration/ "
            f"-v -m integration --junitxml={xml_path}"
        )
        sys.exit(1)

    results = parse_junit_xml(xml_path)
    report = generate_report(results)

    report_path = "validation-report.md"
    Path(report_path).write_text(report)
    print(f"Report written to {report_path}")
    print(report)
