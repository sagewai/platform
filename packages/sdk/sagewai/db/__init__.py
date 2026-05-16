# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Database layer — SQLAlchemy models and Alembic migrations.

Provides declarative SQLAlchemy models as the single source of truth for
the database schema. Alembic uses these models to auto-generate migrations.

Usage::

    from sagewai.db.models import Base, AgentRun, PromptLog
    from sagewai.db.engine import create_engine
"""
