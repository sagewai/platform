# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Doctor CLI command — check Sagewai installation health."""

from __future__ import annotations

import os

import click


def _check_dep(module_name: str, description: str, extra: str) -> None:
    """Check if a Python module is importable."""
    try:
        __import__(module_name)
        click.echo(f"  {click.style('OK', fg='green')} {description}: installed")
    except ImportError:
        click.echo(
            f"  {click.style('--', fg='yellow')} {description}: "
            f"not installed (pip install sagewai{extra})"
        )


def _check_infra(name: str, host: str, port: int, env_var: str) -> None:
    """Check TCP connectivity to an infrastructure service."""
    import socket

    actual_host = host
    url = os.environ.get(env_var, "")
    if url:
        # Parse host from URL like postgresql://user:pass@host:port/db
        parts = url.split("@")
        if len(parts) > 1:
            host_part = parts[-1].split("/")[0].split(":")[0]
            if host_part:
                actual_host = host_part

    try:
        with socket.create_connection((actual_host, port), timeout=2):
            click.echo(
                f"  {click.style('OK', fg='green')} {name}: "
                f"connected ({actual_host}:{port})"
            )
    except (OSError, socket.timeout):
        click.echo(
            f"  {click.style('--', fg='yellow')} {name}: "
            f"not reachable ({actual_host}:{port})"
        )


def _check_env(var: str, name: str) -> None:
    """Check if an environment variable is set."""
    if os.environ.get(var):
        click.echo(f"  {click.style('OK', fg='green')} {name}: configured")
    else:
        click.echo(
            f"  {click.style('--', fg='yellow')} {name}: {var} not set"
        )


@click.command("doctor")
def doctor() -> None:
    """Check Sagewai installation health — dependencies, backends, and intelligence.

    \b
    Checks:
      - SDK version and exports
      - Intelligence layer dependencies (embeddings, NER, language detection)
      - Multimodal dependencies (transcription, image processing)
      - Infrastructure connectivity (Postgres, Redis, Milvus, NebulaGraph)
      - LLM provider API keys

    \b
    Examples:
      sagewai doctor
    """
    click.echo("Sagewai Doctor\n")

    # 1. Core SDK
    import sagewai

    click.echo(f"  SDK version: {sagewai.__version__}")
    click.echo(f"  Exports: {len(sagewai.__all__)}")

    # 2. Intelligence dependencies
    click.echo("\nIntelligence Layer:")
    _check_dep("sentence_transformers", "Local embeddings (I1)", "[intelligence]")
    _check_dep("gliner", "Entity extraction (I3)", "[intelligence]")
    _check_dep("lingua", "Language detection (I2)", "[intelligence]")
    _check_dep("faster_whisper", "Audio transcription (I6)", "[multimodal]")
    _check_dep("PIL", "Image processing (I6)", "[multimodal]")
    _check_dep(
        "transformers", "BART summarization (I7)", "[intelligence-full]"
    )
    _check_dep("torch", "PyTorch backend", "[intelligence-full]")

    # 3. Infrastructure connectivity
    click.echo("\nInfrastructure:")
    _check_infra("PostgreSQL", "localhost", 5432, "DATABASE_URL")
    _check_infra("Redis", "localhost", 6379, "REDIS_URL")
    _check_infra("Milvus", "localhost", 19530, "MILVUS_URI")
    _check_infra("NebulaGraph", "localhost", 9669, "NEBULA_HOST")

    # 4. LLM providers
    click.echo("\nLLM Providers:")
    _check_env("OPENAI_API_KEY", "OpenAI")
    _check_env("ANTHROPIC_API_KEY", "Anthropic")
    _check_env("GOOGLE_API_KEY", "Google")

    # 5. Summary
    click.echo("\nDoctor check complete.")
