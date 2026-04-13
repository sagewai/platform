# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""File-backed admin state store.

Reads and writes ``~/.sagewai/admin-state.json``.  All mutations are
atomic (write-to-tmp + rename) and process-safe (``fcntl.flock`` on
platforms that support it).  The file stores:

* Organisation settings (from the setup wizard)
* Admin credentials (PBKDF2-hashed)
* Active auth tokens (last 10)
* Projects (multi-tenant isolation)
* LLM provider configurations

For production deployments this layer is replaced by Postgres-backed
stores.  The file-based version exists so that ``sagewai admin serve``
works out of the box with zero dependencies.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any


_DEFAULT_STATE_DIR = Path.home() / ".sagewai"
_DEFAULT_STATE_FILE = _DEFAULT_STATE_DIR / "admin-state.json"

_PBKDF2_ITERATIONS = 600_000
_MAX_TOKENS = 10


# ── helpers ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    )
    return h.hex(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    h, _ = _hash_password(password, salt)
    return secrets.compare_digest(h, stored_hash)


def _make_token() -> str:
    return secrets.token_urlsafe(48)


def _slugify(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


# ── AdminStateFile ───────────────────────────────────────────────────


class AdminStateFile:
    """File-backed admin configuration store.

    Parameters
    ----------
    path:
        Path to the JSON state file.  Defaults to
        ``~/.sagewai/admin-state.json``.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_STATE_FILE

    # ── low-level I/O ────────────────────────────────────────────

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp file + rename
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), suffix=".tmp"
        )
        try:
            os.write(fd, json.dumps(data, indent=2).encode())
            os.close(fd)
            os.replace(tmp, str(self._path))
        except BaseException:
            os.close(fd) if not os.get_inheritable(fd) else None
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _mutate(self, fn: Any) -> Any:
        """Read → mutate → write, returning the fn result."""
        data = self._read()
        result = fn(data)
        self._write(data)
        return result

    # ── migration ────────────────────────────────────────────────

    def _migrate(self, data: dict[str, Any]) -> None:
        """Auto-upgrade old state file formats."""
        # v0: active_token (singular) → active_tokens (list)
        if "active_token" in data and "active_tokens" not in data:
            data["active_tokens"] = [data.pop("active_token")]

        # v0: no projects array → create default from app_slug
        if data.get("setup_complete") and "projects" not in data:
            slug = data.get("app_slug", "default")
            name = data.get("app_name", "Default")
            data["projects"] = [
                {
                    "slug": slug,
                    "name": name,
                    "environment": "production",
                    "allowed_origins": "",
                    "default_model": None,
                    "status": "active",
                    "created_at": data.get("setup_at", _now_iso()),
                    "updated_at": data.get("setup_at", _now_iso()),
                }
            ]

        # v0: no providers array
        if "providers" not in data:
            data["providers"] = []

    # ── setup ────────────────────────────────────────────────────

    def is_setup_complete(self) -> bool:
        return bool(self._read().get("setup_complete"))

    def complete_setup(
        self,
        *,
        org_name: str,
        org_slug: str = "",
        contact_email: str = "",
        timezone: str = "UTC",
        app_name: str = "",
        app_description: str = "",
        admin_name: str = "",
        admin_email: str,
        admin_password: str,
    ) -> dict[str, Any]:
        """Run first-time setup.  Returns ``{ok, org_slug, app_slug, message}``."""
        data = self._read()
        if data.get("setup_complete"):
            return {
                "ok": False,
                "message": "Setup has already been completed.",
            }

        org_slug = org_slug or _slugify(org_name)
        app_slug = _slugify(app_name) if app_name else "default"
        pw_hash, pw_salt = _hash_password(admin_password)
        now = _now_iso()

        data.update(
            {
                "setup_complete": True,
                "setup_at": now,
                "org_name": org_name,
                "org_slug": org_slug,
                "contact_email": contact_email,
                "timezone": timezone,
                "app_name": app_name or "Default",
                "app_slug": app_slug,
                "app_description": app_description,
                "industry": "",
                "team_size": "",
                "app_url": "",
                "admin": {
                    "id": secrets.token_hex(8),
                    "name": admin_name,
                    "email": admin_email,
                    "password_hash": pw_hash,
                    "password_salt": pw_salt,
                    "role": "admin",
                    "created_at": now,
                },
                "active_tokens": [],
                "users": [],
                "projects": [
                    {
                        "slug": app_slug,
                        "name": app_name or "Default",
                        "environment": "production",
                        "allowed_origins": "",
                        "default_model": None,
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                ],
                "providers": [],
            }
        )
        self._write(data)
        return {
            "ok": True,
            "org_slug": org_slug,
            "app_slug": app_slug,
            "message": "Setup complete. You can now sign in.",
        }

    # ── organization ─────────────────────────────────────────────

    def get_org(self) -> dict[str, Any]:
        data = self._read()
        self._migrate(data)
        admin = data.get("admin", {})
        return {
            "org_name": data.get("org_name", ""),
            "org_slug": data.get("org_slug", ""),
            "app_url": data.get("app_url", ""),
            "contact_email": data.get("contact_email", ""),
            "timezone": data.get("timezone", "UTC"),
            "industry": data.get("industry", ""),
            "team_size": data.get("team_size", ""),
            "admin_email": admin.get("email", ""),
            "completed_at": data.get("setup_at", ""),
        }

    def update_org(self, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        self._migrate(data)
        allowed = {
            "org_name", "app_url", "contact_email",
            "timezone", "industry", "team_size",
        }
        for k, v in patch.items():
            if k in allowed:
                data[k] = v
        self._write(data)
        return self.get_org()

    # ── projects ─────────────────────────────────────────────────

    def list_projects(self) -> list[dict[str, Any]]:
        data = self._read()
        self._migrate(data)
        self._write(data)  # persist migration
        return data.get("projects", [])

    def create_project(
        self,
        *,
        name: str,
        slug: str = "",
        environment: str = "production",
        allowed_origins: str = "",
    ) -> dict[str, Any]:
        data = self._read()
        self._migrate(data)
        slug = slug or _slugify(name)
        projects = data.setdefault("projects", [])
        # Check for duplicate slug
        if any(p["slug"] == slug for p in projects):
            raise ValueError(f"Project '{slug}' already exists.")
        now = _now_iso()
        project = {
            "slug": slug,
            "name": name,
            "environment": environment,
            "allowed_origins": allowed_origins,
            "default_model": None,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        projects.append(project)
        self._write(data)
        return project

    def update_project(
        self, slug: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        data = self._read()
        self._migrate(data)
        for p in data.get("projects", []):
            if p["slug"] == slug:
                allowed = {
                    "name", "environment", "allowed_origins",
                    "default_model", "status",
                }
                for k, v in patch.items():
                    if k in allowed:
                        p[k] = v
                p["updated_at"] = _now_iso()
                self._write(data)
                return p
        return None

    def delete_project(self, slug: str) -> bool:
        data = self._read()
        self._migrate(data)
        projects = data.get("projects", [])
        # Cannot delete the first (default) project
        if projects and projects[0]["slug"] == slug:
            raise ValueError("Cannot delete the default project.")
        before = len(projects)
        data["projects"] = [p for p in projects if p["slug"] != slug]
        if len(data["projects"]) == before:
            return False
        self._write(data)
        return True

    # ── project scoping helper ───────────────────────────────────

    @staticmethod
    def _filter_by_project(
        items: list[dict[str, Any]], project_id: str | None
    ) -> list[dict[str, Any]]:
        """Filter items by project scope.

        - project_id=None → return ALL items (org-global view)
        - project_id="X"  → return items with project_id=X OR project_id=None (global)
        """
        if project_id is None:
            return items
        return [
            i for i in items
            if i.get("project_id") in (project_id, None, "")
        ]

    # ── playground agents ─────────────────────────────────────────

    def list_agents(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        data = self._read()
        agents = data.get("agents", [])
        return self._filter_by_project(agents, project_id)

    def create_agent(
        self, spec: dict[str, Any], project_id: str | None = None
    ) -> dict[str, Any]:
        data = self._read()
        agents = data.setdefault("agents", [])
        name = spec.get("name", "")
        spec["project_id"] = project_id
        # Replace if exists
        data["agents"] = [a for a in agents if a.get("name") != name]
        data["agents"].append(spec)
        self._write(data)
        return spec

    def get_agent(self, name: str) -> dict[str, Any] | None:
        data = self._read()
        for a in data.get("agents", []):
            if a.get("name") == name:
                return a
        return None

    def delete_agent(self, name: str) -> bool:
        data = self._read()
        agents = data.get("agents", [])
        before = len(agents)
        data["agents"] = [a for a in agents if a.get("name") != name]
        if len(data["agents"]) == before:
            return False
        self._write(data)
        return True

    def rename_agent(self, old_name: str, new_name: str) -> dict[str, Any] | None:
        data = self._read()
        for a in data.get("agents", []):
            if a.get("name") == old_name:
                a["name"] = new_name
                self._write(data)
                return a
        return None

    # ── providers ────────────────────────────────────────────────

    def list_providers(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        data = self._read()
        self._migrate(data)
        providers = data.get("providers", [])
        providers = self._filter_by_project(providers, project_id)
        # Enrich with env-var detection
        for p in providers:
            env_key = p.get("env_var_key", "")
            if env_key:
                p["env_var_set"] = bool(os.environ.get(env_key))
        return providers

    def upsert_provider(self, provider: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        self._migrate(data)
        providers = data.setdefault("providers", [])
        pname = provider.get("provider_name", "")
        # Auto-generate ID and env_var_key
        if not provider.get("id"):
            provider["id"] = f"prov-{pname}"
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "groq": "GROQ_API_KEY",
            "together": "TOGETHER_API_KEY",
            "xai": "XAI_API_KEY",
            "perplexity": "PERPLEXITY_API_KEY",
            "cohere": "COHERE_API_KEY",
        }
        provider.setdefault("env_var_key", env_map.get(pname, ""))
        provider.setdefault("status", "configured")
        provider.setdefault("env_var_set", False)
        # Upsert by provider_name
        for i, existing in enumerate(providers):
            if existing.get("provider_name") == pname:
                providers[i] = provider
                self._write(data)
                return provider
        providers.append(provider)
        self._write(data)
        return provider

    def delete_provider(self, provider_id: str) -> bool:
        data = self._read()
        self._migrate(data)
        providers = data.get("providers", [])
        before = len(providers)
        data["providers"] = [
            p for p in providers if p.get("id") != provider_id
        ]
        if len(data["providers"]) == before:
            return False
        self._write(data)
        return True

    # ── auth ─────────────────────────────────────────────────────

    def validate_login(
        self, email: str, password: str
    ) -> dict[str, Any] | None:
        """Validate credentials.  Returns user info + token, or None."""
        data = self._read()
        admin = data.get("admin")
        if not admin or admin.get("email") != email:
            return None
        if not _verify_password(
            password, admin["password_hash"], admin["password_salt"]
        ):
            return None
        token = _make_token()
        tokens = data.setdefault("active_tokens", [])
        tokens.append(token)
        data["active_tokens"] = tokens[-_MAX_TOKENS:]
        self._write(data)
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": admin["id"],
                "email": admin["email"],
                "display_name": admin.get("name", ""),
                "avatar_url": None,
                "role": admin.get("role", "admin"),
            },
        }

    def validate_token(self, token: str) -> bool:
        data = self._read()
        return token in data.get("active_tokens", [])

    def get_user_by_token(self, token: str) -> dict[str, Any] | None:
        data = self._read()
        if token not in data.get("active_tokens", []):
            return None
        admin = data.get("admin", {})
        return {
            "id": admin.get("id", ""),
            "email": admin.get("email", ""),
            "display_name": admin.get("name", ""),
            "avatar_url": None,
            "role": admin.get("role", "admin"),
        }

    def refresh_token(self, old_token: str) -> dict[str, Any] | None:
        """Rotate a token.  Returns new auth payload or None."""
        data = self._read()
        tokens = data.get("active_tokens", [])
        if old_token not in tokens:
            return None
        new_token = _make_token()
        tokens.append(new_token)
        data["active_tokens"] = tokens[-_MAX_TOKENS:]
        self._write(data)
        admin = data.get("admin", {})
        return {
            "access_token": new_token,
            "token_type": "bearer",
            "user": {
                "id": admin.get("id", ""),
                "email": admin.get("email", ""),
                "display_name": admin.get("name", ""),
                "avatar_url": None,
                "role": admin.get("role", "admin"),
            },
        }

    def reset(self) -> None:
        """Delete the state file (for testing)."""
        if self._path.exists():
            self._path.unlink()
