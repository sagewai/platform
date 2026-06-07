# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
from __future__ import annotations
import pathlib
import re

ADMIN = pathlib.Path(__file__).resolve().parents[2] / "sagewai" / "admin"
ROUTE_MODULES = sorted(ADMIN.glob("*routes*.py")) + [ADMIN / "serve.py"]


def test_route_modules_do_not_write_state_directly():
    """Route handlers must mutate admin state through the locked AdminStateFile.mutate,
    never via the private _write (which bypasses the file lock)."""
    offenders = []
    for path in ROUTE_MODULES:
        text = path.read_text()
        for m in re.finditer(r"\._write\(", text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        "Direct state _write in route modules (use AdminStateFile.mutate instead):\n"
        + "\n".join(offenders)
    )
