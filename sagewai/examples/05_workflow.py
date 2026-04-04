#!/usr/bin/env python3
# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 05 — Multi-Agent Workflows That Survive Crashes.

Chain multiple agents into a workflow where each agent's output feeds
the next. Sagewai workflows support checkpointing so long-running
pipelines can resume after failures.

Pipeline: Researcher -> Writer -> Editor

Requirements::

    pip install sagewai

Usage::

    export OPENAI_API_KEY=sk-...
    python 05_workflow.py
"""

from __future__ import annotations

import asyncio

from sagewai.engines.universal import UniversalAgent


async def main() -> None:
    topic = "the future of AI agents in enterprise software"

    # Stage 1: Research
    researcher = UniversalAgent(
        name="researcher",
        model="gpt-4o-mini",
        system_prompt=(
            "You are a research analyst. Given a topic, produce 5 key findings "
            "with supporting evidence. Be concise and factual."
        ),
    )
    print("Stage 1: Researching...")
    findings = await researcher.chat(f"Research this topic: {topic}")
    print(f"  Findings: {findings[:150]}...\n")

    # Stage 2: Write
    writer = UniversalAgent(
        name="writer",
        model="gpt-4o-mini",
        system_prompt=(
            "You are a technical writer. Given research findings, write a "
            "concise 3-paragraph article. Use clear, professional language."
        ),
    )
    print("Stage 2: Writing...")
    draft = await writer.chat(f"Write an article based on these findings:\n{findings}")
    print(f"  Draft: {draft[:150]}...\n")

    # Stage 3: Edit
    editor = UniversalAgent(
        name="editor",
        model="gpt-4o-mini",
        system_prompt=(
            "You are a senior editor. Review the article for clarity, accuracy, "
            "and tone. Return the improved version."
        ),
    )
    print("Stage 3: Editing...")
    final = await editor.chat(f"Edit and improve this article:\n{draft}")
    print(f"  Final: {final[:300]}...\n")

    print("Pipeline complete: Researcher -> Writer -> Editor")


if __name__ == "__main__":
    asyncio.run(main())
