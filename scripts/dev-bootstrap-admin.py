#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany.
"""Idempotent dev-bootstrap for the admin state file.

Run from anywhere; produces ``~/.sagewai/admin-state.json`` with:
  * an admin user (email/password)
  * an active token
  * autopilot enabled and pointing at the local sagewai-llm

The Next.js admin obtains its token automatically: the just recipe
runs the backend with ``SAGEWAI_DEV_TRUST_LOCAL=1``, and the browser's
silent-refresh call to ``/api/v1/auth/refresh`` returns a token without
needing a session cookie when the request comes from localhost. No env
file edits, no operator action.

Re-running is safe: existing user / token / autopilot config are
preserved unless ``--reset`` is passed.

Usage::

    uv run --package sagewai python scripts/dev-bootstrap-admin.py
    uv run --package sagewai python scripts/dev-bootstrap-admin.py --reset
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import sys
from pathlib import Path

DEFAULT_EMAIL = os.environ.get("SAGEWAI_DEV_ADMIN_EMAIL", "dev@sagewai.local")
DEFAULT_PASSWORD = os.environ.get("SAGEWAI_DEV_ADMIN_PASSWORD", "sagewai-dev")
DEFAULT_LLM_BASE_URL = os.environ.get(
    "SAGEWAI_LLM_BASE_URL", "http://localhost:8100"
)

STATE_PATH = Path.home() / ".sagewai" / "admin-state.json"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """PBKDF2-HMAC-SHA256, matching sagewai.admin.state_file._hash_password."""
    import hashlib

    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
    return h.hex(), salt


def _load_state() -> dict:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _ensure_admin(state: dict, *, reset: bool) -> tuple[str, str, bool]:
    """Ensure an admin user exists. Returns (email, password, created_new)."""
    if not reset and state.get("admin"):
        admin = state["admin"]
        return admin["email"], "(unchanged)", False

    pw_hash, salt = _hash_password(DEFAULT_PASSWORD)
    state["admin"] = {
        "id": secrets.token_hex(8),
        "name": "Dev Admin",
        "email": DEFAULT_EMAIL,
        "password_hash": pw_hash,
        "password_salt": salt,
        "role": "admin",
        "created_at": _now_iso(),
    }
    return DEFAULT_EMAIL, DEFAULT_PASSWORD, True


def _ensure_token(state: dict, *, reset: bool) -> tuple[str, bool]:
    """Ensure at least one active token exists. Returns (token, minted_new)."""
    tokens = state.get("active_tokens") or []
    if not reset and tokens:
        first = tokens[0]
        token = first if isinstance(first, str) else first.get("token", "")
        if token:
            return token, False
    new_token = secrets.token_urlsafe(48)
    state["active_tokens"] = [new_token, *tokens] if not reset else [new_token]
    return new_token, True


def _ensure_autopilot(state: dict) -> bool:
    """Ensure autopilot is enabled and points at the local server.
    Returns True if anything changed."""
    config = state.get("autopilot") or {}
    desired = {
        "enabled": True,
        "tier": "anonymous",
        "base_url": DEFAULT_LLM_BASE_URL,
    }
    changed = False
    for k, v in desired.items():
        if config.get(k) != v:
            config[k] = v
            changed = True
    state["autopilot"] = config
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Force a fresh admin user + token + autopilot config "
        "(overwrites existing).",
    )
    args = parser.parse_args(argv)

    state = _load_state()
    email, password, admin_new = _ensure_admin(state, reset=args.reset)
    _, token_new = _ensure_token(state, reset=args.reset)
    autopilot_changed = _ensure_autopilot(state)
    _save_state(state)

    print("─── sagewai admin dev-bootstrap ───")
    print(f"  state file:    {STATE_PATH}")
    print(f"  admin email:   {email}")
    print(f"  admin pw:      {password}")
    print(f"  admin user:    {'NEW' if admin_new else 'unchanged'}")
    print(f"  active token:  {'NEW' if token_new else 'unchanged'}")
    print(f"  autopilot:     {'updated' if autopilot_changed else 'unchanged'}"
          f" (base_url={DEFAULT_LLM_BASE_URL})")
    print()
    print("  → The browser obtains its token via /api/v1/auth/refresh")
    print("    automatically when SAGEWAI_DEV_TRUST_LOCAL=1 is set on the")
    print("    backend (the just recipe sets it). No env file edits.")
    if admin_new:
        print(f"  → If you ever need a real login: {email} / {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
