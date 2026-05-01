# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for scope-based access control."""

import pytest

from sagewai.context.access import AccessDeniedError, check_read_access, check_write_access
from sagewai.context.models import ContextScope


class TestWriteAccess:
    def test_org_admin_allowed(self):
        check_write_access(ContextScope.ORG, "org-1", user_id="u1", user_role="admin")

    def test_org_member_denied(self):
        with pytest.raises(AccessDeniedError, match="admin role"):
            check_write_access(ContextScope.ORG, "org-1", user_id="u1", user_role="member")

    def test_project_member_allowed(self):
        check_write_access(ContextScope.PROJECT, "proj-1", user_id="u1", user_role="member")

    def test_project_unauthenticated_denied(self):
        with pytest.raises(AccessDeniedError, match="authentication"):
            check_write_access(ContextScope.PROJECT, "proj-1", user_id=None)

    def test_project_programmatic_allowed(self):
        check_write_access(ContextScope.PROJECT, "proj-1", is_programmatic=True)


class TestReadAccess:
    def test_agent_reads_everything(self):
        for scope in ContextScope:
            check_read_access(scope, "any-id", is_agent=True)

    def test_admin_reads_everything(self):
        for scope in ContextScope:
            check_read_access(scope, "any-id", user_id="admin", user_role="admin")

    def test_member_reads_org(self):
        check_read_access(ContextScope.ORG, "org-1", user_id="u1", user_role="member")

    def test_member_reads_project(self):
        check_read_access(ContextScope.PROJECT, "proj-1", user_id="u1", user_role="member")

    def test_unauthenticated_denied(self):
        with pytest.raises(AccessDeniedError, match="authentication"):
            check_read_access(ContextScope.PROJECT, "proj-1", user_id=None)
