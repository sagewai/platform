# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Per-pillar soak harnesses for the Sagewai v1.0 launch.

Each script in this package runs one pillar's "publishable numbers"
soak. They are dev tools (the underscore prefix excludes them from the
user-facing example numbering) but every sibling ``.md`` is the
operator's contract for what the script proves and how to re-run it.

The reports they emit live in the private companion repo at
``sagewai/atelier:docs/v1.0/<pillar>-soak-report.md`` and are the
artifacts referenced by the launch coordination plan.
"""
