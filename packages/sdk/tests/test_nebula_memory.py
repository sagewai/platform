# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for NebulaGraphMemory — NebulaGraph-backed knowledge graph."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.memory.nebula import NebulaGraphMemory, _escape_ngql

# ---------------------------------------------------------------------------
# _escape_ngql helper
# ---------------------------------------------------------------------------


class TestEscapeNgql:
    def test_escapes_backslash(self):
        assert _escape_ngql("a\\b") == "a\\\\b"

    def test_escapes_double_quotes(self):
        assert _escape_ngql('a"b') == 'a\\"b'

    def test_escapes_single_quotes(self):
        assert _escape_ngql("a'b") == "a\\'b"

    def test_no_escaping_needed(self):
        assert _escape_ngql("hello") == "hello"

    def test_combined_escapes(self):
        result = _escape_ngql('a\\b"c\'d')
        assert "\\\\" in result
        assert '\\"' in result
        assert "\\'" in result


# ---------------------------------------------------------------------------
# _extract_relations (LLM-based)
# ---------------------------------------------------------------------------


class TestExtractRelations:
    @pytest.mark.asyncio
    async def test_extracts_triples(self):
        from sagewai.memory.nebula import _extract_relations_llm as _extract_relations

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='[["Python", "is_a", "Language"]]'
                )
            )
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await _extract_relations("Python is a language")

        assert result == [("Python", "is_a", "Language")]

    @pytest.mark.asyncio
    async def test_handles_code_fenced_json(self):
        from sagewai.memory.nebula import _extract_relations_llm as _extract_relations

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='```json\n[["A", "rel", "B"]]\n```'
                )
            )
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await _extract_relations("A relates to B")

        assert result == [("A", "rel", "B")]

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self):
        from sagewai.memory.nebula import _extract_relations_llm as _extract_relations

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(content="not json at all")
            )
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await _extract_relations("Some text")

        assert result == []

    @pytest.mark.asyncio
    async def test_handles_multiple_triples(self):
        from sagewai.memory.nebula import _extract_relations_llm as _extract_relations

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='[["A", "r1", "B"], ["C", "r2", "D"]]'
                )
            )
        ]

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await _extract_relations("A r1 B and C r2 D")

        assert len(result) == 2
        assert result[0] == ("A", "r1", "B")
        assert result[1] == ("C", "r2", "D")


# ---------------------------------------------------------------------------
# NebulaGraphMemory init
# ---------------------------------------------------------------------------


def _make_pool_mock() -> MagicMock:
    """Create a mock ConnectionPool."""
    pool_instance = MagicMock()
    pool_instance.init.return_value = True
    return pool_instance


def _make_session_mock() -> MagicMock:
    """Create a mock session with default success results."""
    session = MagicMock()
    result = MagicMock()
    result.is_succeeded.return_value = True
    result.row_size.return_value = 0
    session.execute.return_value = result
    return session


class TestInit:
    def test_default_config(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            mock_pool_cls.return_value = _make_pool_mock()
            mem = NebulaGraphMemory()
            assert mem.space == "agent_memory"
            assert mem._initialized is False

    def test_custom_config(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            mock_pool_cls.return_value = _make_pool_mock()
            mem = NebulaGraphMemory(
                host="nebula-host", port=9670, space="custom", user="admin", password="pw"
            )
            assert mem.space == "custom"
            assert mem._user == "admin"
            assert mem._password == "pw"

    def test_raises_without_nebula3(self):
        with patch("sagewai.memory.nebula.ConnectionPool", None):
            with pytest.raises(ImportError, match="nebula3-python"):
                NebulaGraphMemory()


# ---------------------------------------------------------------------------
# _get_session and _init_space
# ---------------------------------------------------------------------------


class TestGetSession:
    def test_initializes_space_on_first_call(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory(space="test_space")
            assert mem._initialized is False

            returned = mem._get_session()
            assert returned is session
            assert mem._initialized is True

            # Should have run CREATE SPACE, USE, CREATE TAG, CREATE EDGE, USE
            calls = [str(c) for c in session.execute.call_args_list]
            assert any("CREATE SPACE" in c for c in calls)
            assert any("CREATE TAG" in c for c in calls)
            assert any("CREATE EDGE" in c for c in calls)

    def test_skips_init_on_subsequent_calls(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory(space="test_space")
            mem._get_session()
            first_call_count = session.execute.call_count

            mem._get_session()
            # Only one more call: USE space
            assert session.execute.call_count == first_call_count + 1


# ---------------------------------------------------------------------------
# add_relation
# ---------------------------------------------------------------------------


class TestAddRelation:
    @pytest.mark.asyncio
    async def test_add_relation_inserts_vertices_and_edge(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory()
            await mem.add_relation("Python", "is_a", "Programming Language")

            calls = [str(c) for c in session.execute.call_args_list]
            insert_vertex_calls = [c for c in calls if "INSERT VERTEX" in c]
            insert_edge_calls = [c for c in calls if "INSERT EDGE" in c]
            assert len(insert_vertex_calls) >= 2  # source + target vertices
            assert len(insert_edge_calls) >= 1

    @pytest.mark.asyncio
    async def test_add_relation_with_properties(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory()
            await mem.add_relation("A", "knows", "B", properties={"since": "2020"})

            calls = [str(c) for c in session.execute.call_args_list]
            edge_calls = [c for c in calls if "INSERT EDGE" in c]
            assert len(edge_calls) >= 1

    @pytest.mark.asyncio
    async def test_add_relation_escapes_values(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory()
            await mem.add_relation('He"llo', "rel", "Wor'ld")

            calls = [str(c) for c in session.execute.call_args_list]
            # Should not have unescaped quotes in nGQL
            for call in calls:
                if "INSERT" in call:
                    assert 'He"llo' not in call or '\\"' in call


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class TestStore:
    @pytest.mark.asyncio
    async def test_store_extracts_and_adds_relations(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory()
            with patch.object(
                mem, "_extract_relations", new_callable=AsyncMock
            ) as mock_extract:
                mock_extract.return_value = [("Python", "is_a", "Language")]
                await mem.store("Python is a programming language")

                mock_extract.assert_called_once_with("Python is a programming language")
                calls = [str(c) for c in session.execute.call_args_list]
                assert any("INSERT EDGE" in c for c in calls)

    @pytest.mark.asyncio
    async def test_store_no_relations_extracted(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory()
            with patch.object(
                mem, "_extract_relations", new_callable=AsyncMock
            ) as mock_extract:
                mock_extract.return_value = []
                await mem.store("Hello world")

                # Only init space calls, no INSERT calls after
                calls = [str(c) for c in session.execute.call_args_list]
                insert_edge_calls = [c for c in calls if "INSERT EDGE" in c]
                assert len(insert_edge_calls) == 0

    @pytest.mark.asyncio
    async def test_store_multiple_relations(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory()
            with patch.object(
                mem, "_extract_relations", new_callable=AsyncMock
            ) as mock_extract:
                mock_extract.return_value = [
                    ("A", "r1", "B"),
                    ("C", "r2", "D"),
                ]
                await mem.store("A r1 B and C r2 D")

                calls = [str(c) for c in session.execute.call_args_list]
                insert_edge_calls = [c for c in calls if "INSERT EDGE" in c]
                assert len(insert_edge_calls) >= 2


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------


class TestRetrieve:
    @pytest.mark.asyncio
    async def test_retrieve_returns_strings(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            # Override execute to return results for the LOOKUP+GO query
            lookup_result = MagicMock()
            lookup_result.is_succeeded.return_value = True
            lookup_result.row_size.return_value = 2
            lookup_result.row_values.side_effect = [
                [
                    MagicMock(as_string=lambda: "Python"),
                    MagicMock(as_string=lambda: "is_a"),
                    MagicMock(as_string=lambda: "Language"),
                ],
                [
                    MagicMock(as_string=lambda: "Python"),
                    MagicMock(as_string=lambda: "used_for"),
                    MagicMock(as_string=lambda: "AI"),
                ],
            ]

            # First calls: init space (CREATE SPACE, USE, CREATE TAG, CREATE EDGE)
            # then USE space, then LOOKUP query
            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result,  # CREATE SPACE
                init_result,  # USE
                init_result,  # CREATE TAG
                init_result,  # CREATE EDGE
                init_result,  # USE (from _get_session)
                lookup_result,  # LOOKUP query
            ]

            mem = NebulaGraphMemory()
            results = await mem.retrieve("Python", top_k=5)

            assert isinstance(results, list)
            assert len(results) == 2
            assert all(isinstance(r, str) for r in results)
            assert "Python is_a Language" in results
            assert "Python used_for AI" in results

    @pytest.mark.asyncio
    async def test_retrieve_empty_result(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            empty_result = MagicMock()
            empty_result.is_succeeded.return_value = True
            empty_result.row_size.return_value = 0

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result,  # CREATE SPACE
                init_result,  # USE
                init_result,  # CREATE TAG
                init_result,  # CREATE EDGE
                init_result,  # USE
                empty_result,  # LOOKUP query
            ]

            mem = NebulaGraphMemory()
            results = await mem.retrieve("nonexistent")
            assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_failed_query(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            fail_result = MagicMock()
            fail_result.is_succeeded.return_value = False
            fail_result.row_size.return_value = 0

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result,  # CREATE SPACE
                init_result,  # USE
                init_result,  # CREATE TAG
                init_result,  # CREATE EDGE
                init_result,  # USE
                fail_result,  # LOOKUP query fails
            ]

            mem = NebulaGraphMemory()
            results = await mem.retrieve("query")
            assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_respects_top_k(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            lookup_result = MagicMock()
            lookup_result.is_succeeded.return_value = True
            lookup_result.row_size.return_value = 3
            lookup_result.row_values.side_effect = [
                [
                    MagicMock(as_string=lambda: "A"),
                    MagicMock(as_string=lambda: "r1"),
                    MagicMock(as_string=lambda: "B"),
                ],
                [
                    MagicMock(as_string=lambda: "C"),
                    MagicMock(as_string=lambda: "r2"),
                    MagicMock(as_string=lambda: "D"),
                ],
                [
                    MagicMock(as_string=lambda: "E"),
                    MagicMock(as_string=lambda: "r3"),
                    MagicMock(as_string=lambda: "F"),
                ],
            ]

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result,  # CREATE SPACE
                init_result,  # USE
                init_result,  # CREATE TAG
                init_result,  # CREATE EDGE
                init_result,  # USE
                lookup_result,  # LOOKUP query
            ]

            mem = NebulaGraphMemory()
            results = await mem.retrieve("A", top_k=2)
            assert len(results) == 2


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------


class TestGetNeighbors:
    @pytest.mark.asyncio
    async def test_get_neighbors_returns_dicts(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            go_result = MagicMock()
            go_result.is_succeeded.return_value = True
            go_result.row_size.return_value = 2
            go_result.row_values.side_effect = [
                [
                    MagicMock(as_string=lambda: "Language"),
                    MagicMock(as_string=lambda: "is_a"),
                ],
                [
                    MagicMock(as_string=lambda: "AI"),
                    MagicMock(as_string=lambda: "used_for"),
                ],
            ]

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result,  # CREATE SPACE
                init_result,  # USE
                init_result,  # CREATE TAG
                init_result,  # CREATE EDGE
                init_result,  # USE
                go_result,  # GO query
            ]

            mem = NebulaGraphMemory()
            neighbors = await mem.get_neighbors("Python", depth=1)

            assert len(neighbors) == 2
            assert neighbors[0] == {"entity": "Language", "relation": "is_a"}
            assert neighbors[1] == {"entity": "AI", "relation": "used_for"}

    @pytest.mark.asyncio
    async def test_get_neighbors_empty(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            empty_result = MagicMock()
            empty_result.is_succeeded.return_value = True
            empty_result.row_size.return_value = 0

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result, init_result, init_result, init_result,
                init_result, empty_result,
            ]

            mem = NebulaGraphMemory()
            neighbors = await mem.get_neighbors("Nonexistent")
            assert neighbors == []

    @pytest.mark.asyncio
    async def test_get_neighbors_failed_query(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            fail_result = MagicMock()
            fail_result.is_succeeded.return_value = False
            fail_result.row_size.return_value = 0

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result, init_result, init_result, init_result,
                init_result, fail_result,
            ]

            mem = NebulaGraphMemory()
            neighbors = await mem.get_neighbors("X")
            assert neighbors == []

    @pytest.mark.asyncio
    async def test_get_neighbors_custom_depth(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            go_result = MagicMock()
            go_result.is_succeeded.return_value = True
            go_result.row_size.return_value = 1
            go_result.row_values.return_value = [
                MagicMock(as_string=lambda: "Deep"),
                MagicMock(as_string=lambda: "connects"),
            ]

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result, init_result, init_result, init_result,
                init_result, go_result,
            ]

            mem = NebulaGraphMemory()
            await mem.get_neighbors("Start", depth=3)

            # Verify the GO query uses the right depth
            go_call = str(session.execute.call_args_list[-1])
            assert "3 STEPS" in go_call


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_returns_true_on_success(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            success_result = MagicMock()
            success_result.is_succeeded.return_value = True

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result, init_result, init_result, init_result,
                init_result, success_result,
            ]

            mem = NebulaGraphMemory()
            result = await mem.delete("Python")
            assert result is True

            # Should have executed DELETE VERTEX ... WITH EDGE
            delete_call = str(session.execute.call_args_list[-1])
            assert "DELETE VERTEX" in delete_call
            assert "WITH EDGE" in delete_call

    @pytest.mark.asyncio
    async def test_delete_returns_false_on_failure(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            fail_result = MagicMock()
            fail_result.is_succeeded.return_value = False

            init_result = MagicMock()
            init_result.is_succeeded.return_value = True
            init_result.row_size.return_value = 0

            session.execute.side_effect = [
                init_result, init_result, init_result, init_result,
                init_result, fail_result,
            ]

            mem = NebulaGraphMemory()
            result = await mem.delete("Nonexistent")
            assert result is False


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_drops_space_and_resets_flag(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory(space="test_space")
            mem._initialized = True
            await mem.clear()

            assert mem._initialized is False
            calls = [str(c) for c in session.execute.call_args_list]
            assert any("DROP SPACE" in c for c in calls)
            assert any("test_space" in c for c in calls)

    @pytest.mark.asyncio
    async def test_clear_when_not_initialized(self):
        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            pool = _make_pool_mock()
            session = _make_session_mock()
            pool.get_session.return_value = session
            mock_pool_cls.return_value = pool

            mem = NebulaGraphMemory()
            await mem.clear()
            assert mem._initialized is False


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_satisfies_memory_provider(self):
        from sagewai.memory import MemoryProvider

        with patch("sagewai.memory.nebula.ConnectionPool") as mock_pool_cls:
            mock_pool_cls.return_value = _make_pool_mock()
            mem = NebulaGraphMemory()
            assert isinstance(mem, MemoryProvider)
