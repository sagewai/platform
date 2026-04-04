# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Eval dataset — collections of test cases."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalCase:
    """A single evaluation scenario."""

    input: str
    agent_name: str
    criteria: list[str]
    expected_output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "input": self.input,
            "agent_name": self.agent_name,
            "criteria": self.criteria,
        }
        if self.expected_output is not None:
            d["expected_output"] = self.expected_output
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class EvalDataset:
    """A collection of eval cases."""

    cases: list[EvalCase] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cases)

    @classmethod
    def from_jsonl(cls, path: str) -> EvalDataset:
        """Load cases from a JSONL file."""
        cases: list[EvalCase] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                cases.append(EvalCase(
                    input=data["input"],
                    agent_name=data["agent_name"],
                    criteria=data["criteria"],
                    expected_output=data.get("expected_output"),
                    metadata=data.get("metadata", {}),
                ))
        return cls(cases=cases)

    def to_jsonl(self, path: str) -> None:
        """Save cases to a JSONL file."""
        with open(path, "w") as f:
            for case in self.cases:
                f.write(json.dumps(case.to_dict()) + "\n")
