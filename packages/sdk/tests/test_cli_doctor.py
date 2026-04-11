"""Tests for the ``sagewai doctor`` CLI command."""

from __future__ import annotations

from click.testing import CliRunner

from sagewai.cli import cli


def test_doctor_runs_without_error() -> None:
    """Doctor command should exit with code 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert result.exit_code == 0, result.output


def test_doctor_shows_version() -> None:
    """Doctor output should include the SDK version."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert "SDK version:" in result.output
    assert "0.1.0" in result.output


def test_doctor_shows_exports_count() -> None:
    """Doctor output should include the exports count."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert "Exports:" in result.output


def test_doctor_shows_intelligence_section() -> None:
    """Doctor output should include the intelligence layer section."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert "Intelligence Layer:" in result.output
    assert "Local embeddings" in result.output
    assert "Entity extraction" in result.output
    assert "Language detection" in result.output


def test_doctor_shows_infrastructure_section() -> None:
    """Doctor output should include the infrastructure section."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert "Infrastructure:" in result.output
    assert "PostgreSQL" in result.output
    assert "Redis" in result.output
    assert "Milvus" in result.output
    assert "NebulaGraph" in result.output


def test_doctor_shows_llm_providers_section() -> None:
    """Doctor output should include the LLM providers section."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert "LLM Providers:" in result.output
    assert "OpenAI" in result.output
    assert "Anthropic" in result.output
    assert "Google" in result.output


def test_doctor_shows_summary() -> None:
    """Doctor output should end with a completion summary."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor"])
    assert "Doctor check complete." in result.output


def test_cli_version_option() -> None:
    """CLI should support --version flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
