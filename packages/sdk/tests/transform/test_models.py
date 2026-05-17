# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the transform data models."""

from sagewai.transform.models import TransformRequest, TransformResult


def test_request_defaults():
    req = TransformRequest(operation="summarize", content="hello")
    assert req.params == {}
    assert req.project_id is None


def test_result_ok_and_error():
    ok = TransformResult(operation="summarize", output="short", ok=True)
    assert ok.ok and ok.error is None and ok.metadata == {}
    bad = TransformResult(operation="graphify", output="", ok=False, error="boom")
    assert not bad.ok and bad.error == "boom"
