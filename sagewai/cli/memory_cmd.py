# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Memory CLI commands — vector search, graph queries, and ingestion."""

from __future__ import annotations

import click

from sagewai.cli._helpers import _api_get, _api_post, _echo_json


@click.group("memory")
def memory() -> None:
    """Manage memory stores — vector search, graph queries, and ingestion.

    \b
    Examples:
      sagewai memory vector-stats              Show vector store statistics
      sagewai memory vector-search "AI agents" Search the vector store
      sagewai memory graph-stats               Show knowledge graph statistics
      sagewai memory graph-entity "Sagewai"    Look up a graph entity
    """


@memory.command("vector-stats")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def memory_vector_stats(as_json: bool) -> None:
    """Show vector store statistics."""
    data = _api_get("/api/v1/memory/vector/stats")
    if as_json:
        _echo_json(data)
        return
    click.echo("Vector Store Stats")
    click.echo("=" * 30)
    click.echo(f"  Status    : {data.get('status', '—')}")
    click.echo(f"  Documents : {data.get('documents', 0)}")
    click.echo(f"  Backend   : {data.get('backend', '—')}")


@memory.command("vector-search")
@click.argument("query")
@click.option("--top-k", default=5, help="Number of results.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def memory_vector_search(query: str, top_k: int, as_json: bool) -> None:
    """Search the vector store."""
    data = _api_post(
        "/api/v1/memory/vector/search", {"query": query, "top_k": top_k}
    )
    if as_json:
        _echo_json(data)
        return
    results = data.get("results", [])
    click.echo(f"Found {data.get('count', 0)} results for: {query}\n")
    for r in results:
        click.echo(f"  [{r.get('rank', '?')}] {r.get('content', '')[:100]}")


@memory.command("vector-ingest")
@click.argument("content")
def memory_vector_ingest(content: str) -> None:
    """Ingest text into the vector store."""
    _api_post("/api/v1/memory/vector/ingest", {"content": content})
    click.echo("Document ingested into vector store.")


@memory.command("graph-stats")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def memory_graph_stats(as_json: bool) -> None:
    """Show knowledge graph statistics."""
    data = _api_get("/api/v1/memory/graph/stats")
    if as_json:
        _echo_json(data)
        return
    click.echo("Knowledge Graph Stats")
    click.echo("=" * 30)
    click.echo(f"  Status    : {data.get('status', '—')}")
    click.echo(f"  Entities  : {data.get('entities', 0)}")
    click.echo(f"  Relations : {data.get('relations', 0)}")
    click.echo(f"  Backend   : {data.get('backend', '—')}")


@memory.command("graph-query")
@click.argument("query")
@click.option("--top-k", default=5, help="Number of results.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def memory_graph_query(query: str, top_k: int, as_json: bool) -> None:
    """Query the knowledge graph."""
    data = _api_post(
        "/api/v1/memory/graph/query", {"query": query, "top_k": top_k}
    )
    if as_json:
        _echo_json(data)
        return
    results = data.get("results", [])
    click.echo(f"Found {data.get('count', 0)} results for: {query}\n")
    for r in results:
        click.echo(f"  [{r.get('rank', '?')}] {r.get('content', '')[:100]}")


@memory.command("graph-entity")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def memory_graph_entity(name: str, as_json: bool) -> None:
    """Look up an entity in the knowledge graph."""
    data = _api_get(f"/api/v1/memory/graph/entity/{name}")
    if as_json:
        _echo_json(data)
        return
    click.echo(f"Entity: {data.get('name', name)}")
    metadata = data.get("metadata", {})
    if metadata:
        for k, v in metadata.items():
            click.echo(f"  {k}: {v}")
