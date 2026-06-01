#!/usr/bin/env python3
"""Compute the next semantic version from a base version + bump level.

Usage:
    next_version.py <current> <major|minor|patch>

<current> may carry a leading 'v' and/or a PEP 440 pre-release/dev suffix
(e.g. 'v1.2.3', '1.2.3rc1', '1.2.3.dev9'); only the X.Y.Z core is used.
Prints the bumped 'X.Y.Z' to stdout.
"""
from __future__ import annotations

import re
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    current, bump = sys.argv[1], sys.argv[2]
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", current.strip())
    if not m:
        print(f"error: cannot parse version core from {current!r}", file=sys.stderr)
        return 2
    major, minor, patch = (int(x) for x in m.groups())
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    elif bump == "patch":
        patch += 1
    else:
        print(f"error: unknown bump {bump!r} (use major|minor|patch)", file=sys.stderr)
        return 2
    print(f"{major}.{minor}.{patch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
