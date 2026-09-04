# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Every work test starts and ends without a process-wide database engine.

The stack builders reach the durability store through ``factory.get_workflow_store()``,
which caches one engine per process; an admin test's engine must not leak into a work
test that binds its own.
"""

import pytest

from sagewai.db import factory


@pytest.fixture(autouse=True)
def _fresh_process_engine():
    factory.reset_engine()
    yield
    factory.reset_engine()
