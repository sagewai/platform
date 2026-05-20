# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import pytest
from sagewai.tools import executors


def test_get_returns_callable_for_every_kind():
    for k in ("sdk", "http", "mcp", "cli", "webhook"):
        assert callable(executors.get(k))


def test_get_raises_on_unknown_kind():
    with pytest.raises(ValueError):
        executors.get("telepathy")
