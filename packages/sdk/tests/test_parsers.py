# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for document and code parsers."""

import pytest

from sagewai.context.parsers import (
    _parse_as_text,
    _parse_code_basic,
    detect_mime_type,
    is_code_file,
)


class TestMimeDetection:
    def test_python_file(self):
        assert detect_mime_type("main.py") == "text/x-python"

    def test_javascript_file(self):
        assert detect_mime_type("app.js") == "text/javascript"

    def test_typescript_file(self):
        assert detect_mime_type("index.tsx") == "text/typescript"

    def test_pdf_file(self):
        mime = detect_mime_type("report.pdf")
        assert mime == "application/pdf"

    def test_markdown_file(self):
        assert detect_mime_type("README.md") == "text/markdown"

    def test_json_file(self):
        assert detect_mime_type("config.json") == "application/json"

    def test_unknown_extension(self):
        mime = detect_mime_type("file.xyz123")
        assert mime is not None  # should return something, not None


class TestIsCodeFile:
    def test_python_is_code(self):
        assert is_code_file("text/x-python") is True

    def test_javascript_is_code(self):
        assert is_code_file("text/javascript") is True

    def test_pdf_is_not_code(self):
        assert is_code_file("application/pdf") is False

    def test_plain_text_is_not_code(self):
        assert is_code_file("text/plain") is False


class TestTextFallback:
    def test_parse_as_text_utf8(self):
        content = b"Hello, world!"
        result = _parse_as_text(content, "test.txt")
        assert result.text == "Hello, world!"
        assert result.metadata["filename"] == "test.txt"
        assert result.metadata["parser"] == "text_fallback"

    def test_parse_as_text_with_unicode(self):
        content = "Héllo wörld 日本語".encode("utf-8")
        result = _parse_as_text(content, "unicode.txt")
        assert "Héllo" in result.text

    def test_parse_as_text_invalid_utf8(self):
        content = b"\xff\xfe invalid bytes"
        result = _parse_as_text(content, "binary.dat")
        assert result.text is not None  # should not raise


class TestBasicCodeParsing:
    def test_python_functions(self):
        code = """
def hello():
    pass

async def world():
    pass

class MyClass:
    def method(self):
        pass
"""
        result = _parse_code_basic(code, "python", "test.py")
        names = {e.name for e in result.entities}
        assert "hello" in names
        assert "world" in names
        assert "MyClass" in names
        assert "method" in names

    def test_python_imports(self):
        code = """
import os
from pathlib import Path
"""
        result = _parse_code_basic(code, "python", "test.py")
        kinds = {e.kind for e in result.entities}
        assert "import" in kinds

    def test_python_class_parent_tracking(self):
        code = """
class Foo:
    def bar(self):
        pass
"""
        result = _parse_code_basic(code, "python", "test.py")
        bar = next((e for e in result.entities if e.name == "bar"), None)
        assert bar is not None
        assert bar.parent == "Foo"

    def test_empty_code(self):
        result = _parse_code_basic("", "python", "empty.py")
        assert result.entities == []
        assert result.language == "python"

    def test_line_numbers(self):
        code = """def first():
    pass

def second():
    pass
"""
        result = _parse_code_basic(code, "python", "test.py")
        first = next(e for e in result.entities if e.name == "first")
        second = next(e for e in result.entities if e.name == "second")
        assert first.start_line < second.start_line
