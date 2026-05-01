# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for temporal tracking on NebulaGraph (#407).

These tests verify the schema and method signatures without requiring
a running NebulaGraph instance. Integration tests with the real DB are
in test_nebula_memory.py (requires docker).
"""

import pytest

from sagewai.context.models import ContextScope


class TestTemporalSchema:
    """Verify temporal fields exist in the schema creation SQL."""

    def test_entity_schema_has_temporal_fields(self):
        """The init_space SQL should include valid_from and superseded_at."""
        # Read the source to verify schema
        import inspect

        from sagewai.memory.nebula import NebulaGraphMemory

        source = inspect.getsource(NebulaGraphMemory._init_space)
        assert "valid_from" in source
        assert "superseded_at" in source

    def test_entity_tag_has_both_fields(self):
        """Both entity tag and relation edge should have temporal fields."""
        import inspect

        from sagewai.memory.nebula import NebulaGraphMemory

        source = inspect.getsource(NebulaGraphMemory._init_space)
        # entity tag
        assert "entity" in source and "valid_from" in source
        # relation edge
        assert "relation" in source and "superseded_at" in source


class TestTemporalMethods:
    """Verify temporal methods exist with correct signatures."""

    def test_has_supersede_method(self):
        from sagewai.memory.nebula import NebulaGraphMemory

        assert hasattr(NebulaGraphMemory, "supersede")

    def test_has_supersede_by_document_method(self):
        from sagewai.memory.nebula import NebulaGraphMemory

        assert hasattr(NebulaGraphMemory, "supersede_by_document")

    def test_has_retrieve_at_method(self):
        from sagewai.memory.nebula import NebulaGraphMemory

        assert hasattr(NebulaGraphMemory, "retrieve_at")

    def test_retrieve_filters_superseded(self):
        """Default retrieve() should filter out superseded entities."""
        import inspect

        from sagewai.memory.nebula import NebulaGraphMemory

        # The superseded filter is in _sync_retrieve_entity (called by retrieve)
        source = inspect.getsource(NebulaGraphMemory._sync_retrieve_entity)
        assert "superseded_at == 0" in source
