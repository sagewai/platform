# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""
Agent engine implementations.

- UniversalAgent: LiteLLM-based implementation for broad compatibility
- GoogleNativeAgent: Google GenAI SDK optimized implementation
"""

from sagewai.engines.google_native import GoogleNativeAgent
from sagewai.engines.universal import UniversalAgent

__all__ = ["UniversalAgent", "GoogleNativeAgent"]
