# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Init CLI command — scaffold a new Sagewai project."""

from __future__ import annotations

from pathlib import Path

import click


@click.command()
@click.argument("project_name")
@click.option("--model", default="gpt-4o", help="Default model for the agent.")
def init(project_name: str, model: str) -> None:
    """Scaffold a new Sagewai project directory.

    Creates a minimal project structure with a sample agent, config,
    and pyproject.toml.
    """
    project_dir = Path.cwd() / project_name

    if project_dir.exists():
        click.echo(f"Error: directory '{project_name}' already exists.", err=True)
        raise SystemExit(1)

    project_dir.mkdir(parents=True)
    (project_dir / "agents").mkdir()
    (project_dir / "tools").mkdir()
    (project_dir / "tests").mkdir()

    # pyproject.toml
    (project_dir / "pyproject.toml").write_text(f"""[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["sagewai>=0.1.0"]

[project.scripts]
{project_name} = "{project_name}.main:main"
""")

    # main.py
    (project_dir / "main.py").write_text(f"""\"\"\"Entry point for {project_name}.\"\"\"

import asyncio
from sagewai.engines.universal import UniversalAgent


async def run() -> None:
    agent = UniversalAgent(
        name="{project_name}-agent",
        model="{model}",
        system_prompt="You are a helpful assistant.",
    )
    result = await agent.chat("Hello!")
    print(result)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
""")

    # sample agent
    (project_dir / "agents" / "sample_agent.py").write_text(
        f"""\"\"\"Sample agent for {project_name}.\"\"\"

from sagewai.engines.universal import UniversalAgent


def create_agent() -> UniversalAgent:
    return UniversalAgent(
        name="sample",
        model="{model}",
        system_prompt="You are a helpful assistant for {project_name}.",
    )
"""
    )

    # .env template
    (project_dir / ".env.example").write_text("""# LLM provider keys
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=

# Database (optional)
SAGEWAI_DATABASE_URL=postgresql://user:pass@localhost:5432/sagewai
""")

    # tests/__init__.py
    (project_dir / "tests" / "__init__.py").write_text("")

    click.echo(f"Created project '{project_name}' at {project_dir}")
    click.echo("\nNext steps:")
    click.echo(f"  cd {project_name}")
    click.echo("  uv sync")
    click.echo("  cp .env.example .env  # add your API keys")
    click.echo("  python main.py")
