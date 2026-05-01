# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
from sagewai.memory.strategies.base import TurnEvent, ExtractedRecord


def test_turn_event_required_fields():
    ev = TurnEvent(role="user", content="I prefer dark mode", session_id="s1")
    assert ev.role == "user"
    assert ev.content == "I prefer dark mode"
    assert ev.session_id == "s1"
    assert ev.metadata == {}


def test_extracted_record_namespace_required():
    rec = ExtractedRecord(
        namespace="preferences",
        content="user prefers dark mode",
        source_session="s1",
        strategy="preference",
    )
    assert rec.namespace == "preferences"
    assert rec.confidence == 1.0
