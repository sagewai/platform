# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Document and code parsers — Docling for documents, tree-sitter for code."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any

from sagewai.context.models import CodeEntity, ParsedCode, ParsedDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MIME type detection
# ---------------------------------------------------------------------------

# Map file extensions to MIME types for common formats
_EXT_TO_MIME: dict[str, str] = {
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".jsx": "text/javascript",
    ".java": "text/x-java",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".rb": "text/x-ruby",
    ".php": "text/x-php",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".cs": "text/x-csharp",
    ".swift": "text/x-swift",
    ".kt": "text/x-kotlin",
    ".dart": "text/x-dart",
    ".sql": "text/x-sql",
    ".sh": "text/x-shellscript",
    ".yaml": "text/x-yaml",
    ".yml": "text/x-yaml",
    ".json": "application/json",
    ".xml": "application/xml",
    ".toml": "text/x-toml",
    ".md": "text/markdown",
    ".rst": "text/x-rst",
    ".txt": "text/plain",
    ".csv": "text/csv",
}

_CODE_MIMES = {
    "text/x-python",
    "text/javascript",
    "text/typescript",
    "text/x-java",
    "text/x-go",
    "text/x-rust",
    "text/x-ruby",
    "text/x-php",
    "text/x-c",
    "text/x-c++",
    "text/x-csharp",
    "text/x-swift",
    "text/x-kotlin",
    "text/x-dart",
    "text/x-sql",
    "text/x-shellscript",
}

# tree-sitter language mapping
_MIME_TO_TS_LANG: dict[str, str] = {
    "text/x-python": "python",
    "text/javascript": "javascript",
    "text/typescript": "typescript",
    "text/x-java": "java",
    "text/x-go": "go",
    "text/x-rust": "rust",
    "text/x-ruby": "ruby",
    "text/x-c": "c",
    "text/x-c++": "cpp",
    "text/x-csharp": "c_sharp",
}


def detect_mime_type(filename: str) -> str:
    """Detect MIME type from filename extension."""
    ext = Path(filename).suffix.lower()
    if ext in _EXT_TO_MIME:
        return _EXT_TO_MIME[ext]
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def is_code_file(mime_type: str) -> bool:
    """Check if a MIME type represents a code file."""
    return mime_type in _CODE_MIMES


# ---------------------------------------------------------------------------
# Document parsing (Docling)
# ---------------------------------------------------------------------------


async def parse_document(
    file_bytes: bytes, mime_type: str, filename: str = "", timeout: float = 120.0
) -> ParsedDocument:
    """Parse a document using Docling (PDF, DOCX, PPTX, XLSX, HTML, images).

    Falls back to plain text decoding if Docling is unavailable or times out.
    """
    try:
        return await asyncio.wait_for(
            _parse_with_docling(file_bytes, mime_type, filename),
            timeout=timeout,
        )
    except ImportError:
        logger.info("Docling not available, falling back to plain text extraction")
        return _parse_as_text(file_bytes, filename)
    except asyncio.TimeoutError:
        logger.warning("Docling timed out after %.0fs for %s, falling back to text", timeout, filename)
        return _parse_as_text(file_bytes, filename)
    except Exception:
        logger.warning("Docling parsing failed for %s, falling back to plain text", filename, exc_info=True)
        return _parse_as_text(file_bytes, filename)


async def _parse_with_docling(
    file_bytes: bytes, mime_type: str, filename: str
) -> ParsedDocument:
    """Parse document using Docling library."""
    from docling.document_converter import DocumentConverter

    with tempfile.NamedTemporaryFile(
        suffix=Path(filename).suffix or ".bin", delete=False
    ) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    def _sync_convert(path: str) -> tuple[str, dict[str, Any]]:
        converter = DocumentConverter()
        result = converter.convert(path)
        text = result.document.export_to_markdown()
        meta: dict[str, Any] = {
            "filename": filename,
            "mime_type": mime_type,
            "parser": "docling",
        }
        if hasattr(result.document, "pages"):
            meta["page_count"] = len(result.document.pages)
        return text, meta

    try:
        text, metadata = await asyncio.to_thread(_sync_convert, tmp_path)
        return ParsedDocument(text=text, metadata=metadata, mime_type=mime_type)
    finally:
        os.unlink(tmp_path)


def _parse_as_text(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Fallback: decode bytes as UTF-8 text."""
    text = file_bytes.decode("utf-8", errors="replace")
    return ParsedDocument(
        text=text,
        metadata={"filename": filename, "parser": "text_fallback"},
        mime_type="text/plain",
    )


# ---------------------------------------------------------------------------
# Code parsing (tree-sitter)
# ---------------------------------------------------------------------------


async def parse_code(content: str, language: str, filename: str = "") -> ParsedCode:
    """Parse code using tree-sitter for AST-level entity extraction.

    Falls back to basic regex extraction if tree-sitter is unavailable.
    """
    try:
        return await _parse_with_tree_sitter(content, language, filename)
    except ImportError:
        logger.info("tree-sitter not available, using basic code extraction")
        return _parse_code_basic(content, language, filename)
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning("tree-sitter parsing failed, using basic extraction", exc_info=True)
        return _parse_code_basic(content, language, filename)


async def _parse_with_tree_sitter(
    content: str, language: str, filename: str
) -> ParsedCode:
    """Parse code using tree-sitter library."""
    import tree_sitter_languages

    def _sync_parse() -> list[CodeEntity]:
        parser = tree_sitter_languages.get_parser(language)
        tree = parser.parse(content.encode("utf-8"))
        entities: list[CodeEntity] = []
        _extract_entities(tree.root_node, entities, language)
        return entities

    entities = await asyncio.to_thread(_sync_parse)

    return ParsedCode(
        text=content,
        language=language,
        entities=entities,
        metadata={"filename": filename, "parser": "tree_sitter"},
    )


def _extract_entities(
    node: Any, entities: list[CodeEntity], language: str, parent_name: str | None = None
) -> None:
    """Recursively extract code entities from tree-sitter AST."""
    # Node types that represent entities across common languages
    entity_types = {
        "function_definition": "function",
        "function_declaration": "function",
        "method_definition": "method",
        "method_declaration": "method",
        "class_definition": "class",
        "class_declaration": "class",
        "import_statement": "import",
        "import_from_statement": "import",
        "import_declaration": "import",
        "interface_declaration": "interface",
        "struct_definition": "struct",
        "enum_definition": "enum",
        "type_alias_declaration": "type_alias",
    }

    node_type = node.type
    if node_type in entity_types:
        name = _get_node_name(node)
        if name:
            entities.append(
                CodeEntity(
                    name=name,
                    kind=entity_types[node_type],
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent=parent_name,
                    metadata={"language": language, "node_type": node_type},
                )
            )
            parent_name = name

    for child in node.children:
        _extract_entities(child, entities, language, parent_name)


def _get_node_name(node: Any) -> str | None:
    """Extract the name identifier from an AST node."""
    for child in node.children:
        if child.type in ("identifier", "name", "type_identifier", "property_identifier"):
            return child.text.decode("utf-8")
        # For dotted imports like "from x.y import z"
        if child.type == "dotted_name":
            return child.text.decode("utf-8")
    return None


def _parse_code_basic(content: str, language: str, filename: str) -> ParsedCode:
    """Basic code entity extraction using line-by-line heuristics."""
    import re

    entities: list[CodeEntity] = []
    lines = content.split("\n")

    patterns = {
        "python": {
            "function": re.compile(r"^(?:async\s+)?def\s+(\w+)"),
            "class": re.compile(r"^class\s+(\w+)"),
            "import": re.compile(r"^(?:from\s+\S+\s+)?import\s+(.+)"),
        },
        "javascript": {
            "function": re.compile(
                r"(?:export\s+)?(?:async\s+)?function\s+(\w+)|const\s+(\w+)\s*="
            ),
            "class": re.compile(r"(?:export\s+)?class\s+(\w+)"),
            "import": re.compile(r"import\s+.+\s+from"),
        },
    }

    lang_patterns = patterns.get(language, patterns.get("python", {}))
    current_class: str | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        for kind, pattern in lang_patterns.items():
            m = pattern.match(stripped)
            if m:
                name = next((g for g in m.groups() if g), None)
                if name:
                    if kind == "class":
                        current_class = name
                    entities.append(
                        CodeEntity(
                            name=name,
                            kind=kind,
                            start_line=i + 1,
                            end_line=i + 1,
                            parent=current_class if kind != "class" else None,
                            metadata={"language": language},
                        )
                    )

    return ParsedCode(
        text=content,
        language=language,
        entities=entities,
        metadata={"filename": filename, "parser": "basic"},
    )


# ---------------------------------------------------------------------------
# Directory traversal
# ---------------------------------------------------------------------------


def _should_ignore(path: Path, ignore_patterns: list[str]) -> bool:
    """Check if a path matches any ignore pattern."""
    import fnmatch

    name = path.name
    rel = str(path)
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            return True
    return False


_DEFAULT_IGNORE = [
    "__pycache__",
    "*.pyc",
    ".git",
    ".svn",
    "node_modules",
    ".env",
    ".venv",
    "venv",
    "*.egg-info",
    ".DS_Store",
    "*.lock",
    "dist",
    "build",
    ".next",
    ".cache",
]

_IGNORE_FILES = [
    ".gitignore",
    ".claudeignore",
    ".geminiignore",
    ".codexignore",
    ".gcloudignore",
    ".dockerignore",
]

_SKIP_MIME_PREFIXES = ("video/", "font/")
_SKIP_MIME_EXACT = {
    "application/octet-stream",
    "application/zip",
    "application/gzip",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/java-archive",
    "application/x-sharedlib",
    "application/x-executable",
    "application/wasm",
    "application/x-mach-binary",
    "application/vnd.microsoft.portable-executable",
}


def _is_binary_mime(mime: str) -> bool:
    if any(mime.startswith(p) for p in _SKIP_MIME_PREFIXES):
        return True
    return mime in _SKIP_MIME_EXACT


async def parse_directory(
    path: str,
    patterns: list[str] | None = None,
    ignore: list[str] | None = None,
    max_file_size_bytes: int = 50_000_000,
) -> list[ParsedDocument | ParsedCode]:
    """Walk a directory tree, parsing each file.

    Parameters
    ----------
    path:
        Root directory to traverse.
    patterns:
        Glob patterns to include (e.g., ``["*.py", "*.md"]``). If None, all files.
    ignore:
        Glob patterns to exclude. Merged with default ignore list.
    max_file_size_bytes:
        Skip files larger than this (default 50MB).

    Returns
    -------
    List of ParsedDocument or ParsedCode for each file.
    """
    import fnmatch

    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    ignore_patterns = list(_DEFAULT_IGNORE)
    if ignore:
        ignore_patterns.extend(ignore)

    # Read ignore files if present
    for ignore_filename in _IGNORE_FILES:
        ignore_file = root / ignore_filename
        if ignore_file.is_file():
            for line in ignore_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    ignore_patterns.append(line)

    results: list[ParsedDocument | ParsedCode] = []

    _always_skip = {".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv", "venv"}

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue

        # Check ignore patterns against relative path
        rel_path = file_path.relative_to(root)

        if _always_skip.intersection(rel_path.parts):
            continue
        if any(_should_ignore(Path(part), ignore_patterns) for part in rel_path.parts):
            continue
        if _should_ignore(rel_path, ignore_patterns):
            continue

        # Check include patterns
        if patterns:
            if not any(fnmatch.fnmatch(file_path.name, p) for p in patterns):
                continue

        # Skip large files
        try:
            size = file_path.stat().st_size
            if size > max_file_size_bytes:
                logger.debug("Skipping large file: %s (%d bytes)", rel_path, size)
                continue
        except OSError:
            continue

        mime = detect_mime_type(file_path.name)

        if _is_binary_mime(mime):
            logger.debug("Skipping binary file: %s (mime=%s)", rel_path, mime)
            continue

        file_meta = {
            "relative_path": str(rel_path),
            "absolute_path": str(file_path),
            "file_size": size,
        }

        try:
            content_bytes = file_path.read_bytes()

            if is_code_file(mime):
                text = content_bytes.decode("utf-8", errors="replace")
                ts_lang = _MIME_TO_TS_LANG.get(mime, "")
                if ts_lang:
                    parsed = await parse_code(text, ts_lang, filename=str(rel_path))
                else:
                    parsed = ParsedCode(
                        text=text,
                        language=mime.split("/")[-1].replace("x-", ""),
                        entities=[],
                        metadata={**file_meta, "parser": "text"},
                    )
                parsed.metadata.update(file_meta)
                results.append(parsed)
            else:
                parsed_doc = await parse_document(
                    content_bytes, mime, filename=str(rel_path)
                )
                parsed_doc.metadata.update(file_meta)
                results.append(parsed_doc)

        except (OSError, RuntimeError, ValueError, TypeError, UnicodeDecodeError):
            logger.warning("Failed to parse %s", rel_path, exc_info=True)
            continue

    logger.info("Parsed %d files from %s", len(results), root)
    return results
