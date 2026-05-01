# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""compute_allowed_env: pure function determining the env subset
visible to a specific tool call after ACL is applied."""
from __future__ import annotations

from sagewai.sealed.acl import compute_allowed_env


def test_no_acl_passes_everything() -> None:
    env = {"K1": "v1", "K2": "v2", "DEBUG": "1"}
    secret_keys = {"K1", "K2"}
    filtered, removed = compute_allowed_env(
        full_env=env, secret_keys=secret_keys, acl={}, tool_name="shell",
    )
    assert filtered == env
    assert removed == []


def test_tool_not_in_acl_passes_everything() -> None:
    env = {"K1": "v1", "K2": "v2"}
    secret_keys = {"K1", "K2"}
    acl = {"claude-code": ["K1"]}
    filtered, removed = compute_allowed_env(
        full_env=env, secret_keys=secret_keys, acl=acl, tool_name="shell",
    )
    assert filtered == env
    assert removed == []


def test_tool_in_acl_filters_to_allowlist() -> None:
    env = {"K1": "v1", "K2": "v2", "DEBUG": "1"}
    secret_keys = {"K1", "K2"}
    acl = {"claude-code": ["K1"]}
    filtered, removed = compute_allowed_env(
        full_env=env, secret_keys=secret_keys, acl=acl, tool_name="claude-code",
    )
    # K1 allowed, K2 secret denied, DEBUG always passes (not secret)
    assert set(filtered.keys()) == {"K1", "DEBUG"}
    assert filtered["K1"] == "v1"
    assert filtered["DEBUG"] == "1"
    assert removed == ["K2"]


def test_empty_acl_list_denies_all_secrets() -> None:
    env = {"K1": "v1", "K2": "v2", "DEBUG": "1"}
    secret_keys = {"K1", "K2"}
    acl = {"shell": []}
    filtered, removed = compute_allowed_env(
        full_env=env, secret_keys=secret_keys, acl=acl, tool_name="shell",
    )
    assert filtered == {"DEBUG": "1"}
    assert sorted(removed) == ["K1", "K2"]


def test_non_secret_keys_always_pass() -> None:
    env = {"K_SECRET": "v", "DEBUG": "1", "MAX_TOKENS": "8000"}
    secret_keys = {"K_SECRET"}
    acl = {"claude-code": []}
    filtered, removed = compute_allowed_env(
        full_env=env, secret_keys=secret_keys, acl=acl, tool_name="claude-code",
    )
    assert filtered == {"DEBUG": "1", "MAX_TOKENS": "8000"}
    assert removed == ["K_SECRET"]


def test_acl_listed_key_not_in_secrets_silently_ignored() -> None:
    env = {"K1": "v1"}
    secret_keys = {"K1"}
    acl = {"claude-code": ["K1", "GHOST"]}  # GHOST not in secret_keys
    filtered, removed = compute_allowed_env(
        full_env=env, secret_keys=secret_keys, acl=acl, tool_name="claude-code",
    )
    assert filtered == {"K1": "v1"}
    assert removed == []


def test_duplicate_keys_in_acl_list_deduped() -> None:
    env = {"K1": "v1"}
    secret_keys = {"K1"}
    acl = {"claude-code": ["K1", "K1", "K1"]}
    filtered, _ = compute_allowed_env(
        full_env=env, secret_keys=secret_keys, acl=acl, tool_name="claude-code",
    )
    assert filtered == {"K1": "v1"}


def test_pure_function_deterministic() -> None:
    env = {"K1": "v1", "K2": "v2"}
    secret_keys = {"K1", "K2"}
    acl = {"shell": ["K1"]}
    a, ar = compute_allowed_env(full_env=env, secret_keys=secret_keys, acl=acl, tool_name="shell")
    b, br = compute_allowed_env(full_env=env, secret_keys=secret_keys, acl=acl, tool_name="shell")
    assert a == b
    assert ar == br
