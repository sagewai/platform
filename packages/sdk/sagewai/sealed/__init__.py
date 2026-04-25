# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai Sealed — profile management foundation.

See docs/superpowers/specs/2026-04-25-sealed-i-profile-management-design.md.
"""

from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.refs import register_backend

# Register the built-in backend at import time. Sealed-ii adds more.
# Lazy crypto resolution: BuiltinAdminStoreBackend resolves the master key
# only when get_profile/save_profile actually need it, so importing this
# package does not require a configured master key.
_default_backend = BuiltinAdminStoreBackend()
register_backend(_default_backend)
