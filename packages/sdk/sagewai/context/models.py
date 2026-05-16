# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Context engine data models — scoped documents, chunks, and search results."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ContextScope(str, Enum):
    """Access scope levels for context entries.

    Only two levels exist:
    - **ORG**: visible to all projects within the organization
    - **PROJECT**: visible only to agents within the owning project
    """

    ORG = "org"
    PROJECT = "project"


class ContextSource(str, Enum):
    """Origin of context data."""

    UPLOAD = "upload"
    DIRECTORY = "directory"
    URL = "url"
    CONVERSATION = "conversation"
    WORKFLOW = "workflow"
    RESEARCH = "research"
    MANUAL = "manual"
    EPISODE = "episode"


class ContextDocument(BaseModel):
    """Metadata for an ingested document."""

    id: str = Field(description="UUID identifier")
    scope: ContextScope
    scope_id: str = Field(description="org_id or project_id")
    project_id: str = Field(default="default")
    title: str
    source: ContextSource = ContextSource.UPLOAD
    source_uri: str | None = None
    mime_type: str = "text/plain"
    file_size_bytes: int = 0
    chunk_count: int = 0
    status: Literal["pending", "processing", "ready", "failed", "archived"] = "pending"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    freshness_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = Field(default_factory=list, description="User-defined labels for filtering")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContextChunk(BaseModel):
    """A single chunk of text from a document, with embedding metadata."""

    id: str = Field(description="UUID identifier")
    document_id: str
    scope: ContextScope
    scope_id: str
    project_id: str = "default"
    content: str
    chunk_index: int = 0
    token_count: int = 0
    embedding_model: str = "text-embedding-3-small"
    content_hash: str = Field(description="SHA-256 of content for dedup")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    access_count: int = 0
    last_accessed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChunkingConfig(BaseModel):
    """Configuration for the chunking strategy."""

    strategy: Literal["fixed", "recursive"] = "recursive"
    chunk_size: int = Field(default=800, gt=0, description="Target chunk size in tokens")
    chunk_overlap: int = Field(default=200, ge=0, description="Overlap between chunks in tokens")
    separators: list[str] = Field(
        default_factory=lambda: ["\n\n", "\n", ". ", " "],
        description="Separator hierarchy for recursive splitting",
    )


class ContextSearchResult(BaseModel):
    """A scored search result with provenance."""

    chunk_id: str
    document_id: str
    content: str
    score: float = Field(description="Composite score: similarity + recency + importance")
    scope: ContextScope
    scope_id: str
    document_title: str
    source: ContextSource
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkText(BaseModel):
    """Intermediate representation during chunking pipeline."""

    content: str
    chunk_index: int
    token_count: int
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    """Output from document parsing."""

    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    mime_type: str = "text/plain"


class CodeEntity(BaseModel):
    """A code entity extracted by tree-sitter."""

    name: str
    kind: str = Field(description="function, class, import, module, etc.")
    start_line: int = 0
    end_line: int = 0
    parent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedCode(BaseModel):
    """Output from code parsing with AST entities."""

    text: str
    language: str
    entities: list[CodeEntity] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
