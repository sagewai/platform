# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""File-backed admin state store.

Reads and writes ``$SAGEWAI_HOME/config/admin-state.json``.  All mutations are
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

import copy
import datetime
import hashlib
import hmac
import json
import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX only
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

import logging

from sagewai import home
from sagewai.admin.provider_secrets import FERNET_PREFIX as _FERNET_PREFIX
from sagewai.admin.provider_secrets import has_plaintext_secret as _provider_has_plaintext_secret
from sagewai.admin.provider_secrets import is_encrypted as _provider_is_encrypted
from sagewai.admin.provider_secrets import redact_secrets as _redact_provider
from sagewai.admin.provider_secrets import walk_secret_fields as _walk_secret_fields
from sagewai.artifacts.models import ArtifactDestination
from sagewai.sealed.directives.policies import DirectivesConfig, seed_defaults_if_empty

logger = logging.getLogger("sagewai.admin")

# ── provider secret encryption helpers ──────────────────────────────


def _encrypt_provider(p: dict[str, Any], crypto: Any) -> dict[str, Any]:
    def _enc(parent: dict[str, Any], k: str) -> None:
        v = parent.get(k)
        if isinstance(v, str) and v and not v.startswith(_FERNET_PREFIX):
            parent[k] = crypto.encrypt(v)

    _walk_secret_fields(p.get("config"), _enc)
    return p


def _any_encrypted_provider(providers: list[dict[str, Any]]) -> bool:
    return any(_provider_is_encrypted(p) for p in providers)


def _decrypt_provider(p: dict[str, Any], crypto: Any) -> dict[str, Any]:
    """Decrypt secret fields.  On failure (no key / wrong key / corrupt), NULL the
    field — NEVER leave a ciphertext blob to be passed as a credential."""
    from sagewai.sealed.crypto import SecretCorrupted
    out = copy.deepcopy(p)

    def _dec(parent: dict[str, Any], k: str) -> None:
        v = parent.get(k)
        if isinstance(v, str) and v.startswith(_FERNET_PREFIX):
            if crypto is None:
                parent[k] = None
                return
            try:
                parent[k] = crypto.decrypt(v)
            except SecretCorrupted:
                logger.warning("provider secret decrypt failed; treating as unset")
                parent[k] = None

    _walk_secret_fields(out.get("config"), _dec)
    return out

def default_admin_state_path() -> Path:
    """Resolve the on-disk admin-state path with env override.

    ``SAGEWAI_ADMIN_STATE_FILE`` overrides; default is
    ``$SAGEWAI_HOME/config/admin-state.json``. Used by PR4 connections
    bootstrap so admin routes and CLI both resolve the same file.
    """
    override = os.environ.get("SAGEWAI_ADMIN_STATE_FILE")
    if override:
        return Path(override)
    return home.config_dir() / "admin-state.json"

_PBKDF2_ITERATIONS = 600_000
# Every /auth/refresh rotates the token and appends a new one. The full e2e
# suite fires ~30+ refreshes in one run, which would otherwise evict the
# token saved in Playwright's shared storageState and fail every test that
# loads an authenticated page. 200 is plenty for e2e and for real users who
# may be signed in from several browsers at once.
_MAX_TOKENS = 200


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


_SESSION_TTL_SECONDS = int(os.environ.get("SAGEWAI_ADMIN_SESSION_TTL_SECONDS", str(7 * 24 * 3600)))


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Sentinel scope meaning "org-shared rows only" (project_id IS NULL). Distinct
# from project_id=None, which the file store treats as the legacy "all projects"
# view. Multi-tenant org-scope reads/deletes pass this so org scope never falls
# back to cross-project access. It is a filter argument only — never stored.
SHARED_ONLY = "__sagewai_shared_only__"


def _session_record(raw: str) -> dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "hash": _hash_token(raw),
        "created_at": now.isoformat(),
        "expires_at": (now + datetime.timedelta(seconds=_SESSION_TTL_SECONDS)).isoformat(),
        "last_used": None,
    }


def _record_is_live(rec: dict[str, Any]) -> bool:
    exp = rec.get("expires_at")
    if not exp:
        return True  # no expiry recorded → treat as live (legacy/defensive)
    try:
        return datetime.datetime.now(datetime.timezone.utc) < datetime.datetime.fromisoformat(exp)
    except ValueError:
        return False


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
        ``$SAGEWAI_HOME/config/admin-state.json``.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else default_admin_state_path()

    # ── low-level I/O ────────────────────────────────────────────

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def get_sealed_config(self) -> dict[str, Any]:
        """Return sealed.* sub-tree (system_profile_ref, system_overrides, retention, etc.)."""
        state = self._read()
        return state.get("sealed", {})

    def get_vault_config(self) -> dict[str, Any]:
        """Return sealed.vault.* sub-tree (addr, auth_method, …).

        Returns empty dict if no Vault config is set.
        """
        return self.get_sealed_config().get("vault", {})

    def get_directives_config(self) -> DirectivesConfig:
        """Return directives sub-tree parsed to DirectivesConfig.

        Returns an empty-default DirectivesConfig when the key is absent
        (backward-compatible with all existing admin-state.json files).
        """
        state = self._read()
        raw = state.get("directives") or {}
        config = DirectivesConfig.model_validate(raw) if raw else DirectivesConfig()
        seeded = seed_defaults_if_empty(config)
        if seeded is not config:
            self.set_directives_config(seeded)
        return seeded

    def set_directives_config(self, config: DirectivesConfig) -> None:
        """Persist the directives config back to state."""
        def _apply(state: dict[str, Any]) -> None:
            state["directives"] = config.model_dump(mode="json")
        self._mutate(_apply)

    def get_sandbox_pool_config(self) -> dict[str, Any]:
        """Return sandbox_pool.* sub-tree (pool sizing knobs from Plan 1.5)."""
        state = self._read()
        return dict(state.get("sandbox_pool") or {})

    def set_sandbox_pool_config(self, cfg: dict[str, Any]) -> None:
        """Replace the sandbox_pool config block."""
        def _apply(state: dict[str, Any]) -> None:
            state["sandbox_pool"] = dict(cfg)
        self._mutate(_apply)

    def get_default_credentials_backend(self) -> str:
        """Return the platform-wide default credentials backend.

        Defaults to ``"local"`` for state files that pre-date the
        Connections Platform PR3. PR3 introduced this field.
        """
        return self._read().get("default_credentials_backend", "local")

    def set_default_credentials_backend(self, backend_id: str) -> None:
        """Set the platform-wide default credentials backend.

        Validates against the registry; raises ``UnknownBackendError`` on
        unknown ids.
        """
        # Local import to avoid pulling the connections package into
        # admin module-load (state_file.py is imported very early).
        from sagewai.connections.credentials import get_backend

        get_backend(backend_id)  # raises UnknownBackendError if absent

        def _apply(state: dict[str, Any]) -> None:
            state["default_credentials_backend"] = backend_id
        self._mutate(_apply)

    def get_kubernetes_backend_config(self) -> dict[str, Any]:
        """Return sandbox_backends.kubernetes sub-tree with safe defaults."""
        state = self._read()
        sb = state.get("sandbox_backends") or {}
        k = sb.get("kubernetes") or {}
        return {
            "kubeconfig_path": k.get("kubeconfig_path"),
            "namespace": k.get("namespace", "sagewai"),
            "egress_allowlist": list(k.get("egress_allowlist", [])),
            "use_in_cluster": bool(k.get("use_in_cluster", True)),
        }

    def set_kubernetes_backend_config(
        self,
        *,
        kubeconfig_path: str | None,
        namespace: str,
        egress_allowlist: list[str],
        use_in_cluster: bool,
    ) -> None:
        """Replace the sandbox_backends.kubernetes block."""
        def _apply(state: dict[str, Any]) -> None:
            sb = state.setdefault("sandbox_backends", {})
            sb["kubernetes"] = {
                "kubeconfig_path": kubeconfig_path,
                "namespace": namespace,
                "egress_allowlist": list(egress_allowlist),
                "use_in_cluster": use_in_cluster,
            }
        self._mutate(_apply)

    def get_workflow_sealed_config(self, workflow_name: str) -> dict[str, Any] | None:
        """Return workflow-level sealed config from admin-state.workflows[name].

        Returns None if no workflow record or no sealed config on it.
        """
        state = self._read()
        workflows = state.get("workflows") or {}
        if isinstance(workflows, dict):
            workflow = workflows.get(workflow_name)
        elif isinstance(workflows, list):
            workflow = next((w for w in workflows if w.get("name") == workflow_name), None)
        else:
            return None
        if not workflow:
            return None
        profile_ref = workflow.get("security_profile_ref")
        overrides = workflow.get("security_overrides")
        if profile_ref is None and not overrides:
            return None
        return {"profile_ref": profile_ref, "overrides": overrides or {}}

    def _workflow_block(
        self, state: dict[str, Any], workflow_name: str,
    ) -> dict[str, Any] | None:
        """Return the workflow record (dict-or-list shape), or None."""
        workflows = state.get("workflows") or {}
        if isinstance(workflows, dict):
            return workflows.get(workflow_name)
        if isinstance(workflows, list):
            return next(
                (w for w in workflows if w.get("name") == workflow_name), None,
            )
        return None

    def get_workflow_artifact_destination(
        self, workflow_name: str,
    ) -> ArtifactDestination | None:
        """Return the admin-override artifact destination for a workflow.

        Plan ART. Returns None when no override is set.
        """
        state = self._read()
        block = self._workflow_block(state, workflow_name)
        if not block:
            return None
        raw = block.get("artifact_destination")
        if raw is None:
            return None
        return ArtifactDestination.model_validate(raw)

    def set_workflow_artifact_destination(
        self, workflow_name: str, destination: ArtifactDestination,
    ) -> None:
        """Persist an admin-override artifact destination for a workflow."""
        def _apply(state: dict[str, Any]) -> None:
            workflows = state.setdefault("workflows", {})
            if isinstance(workflows, list):
                # Normalise list-shaped state to dict-shaped on first ART write
                state["workflows"] = {w.get("name"): w for w in workflows if w.get("name")}
                workflows = state["workflows"]
            block = workflows.get(workflow_name) or {}
            block["artifact_destination"] = destination.model_dump(mode="json")
            workflows[workflow_name] = block
        self._mutate(_apply)

    def clear_workflow_artifact_destination(self, workflow_name: str) -> None:
        """Remove the admin-override artifact destination for a workflow."""
        def _apply(state: dict[str, Any]) -> None:
            workflows = state.get("workflows") or {}
            if isinstance(workflows, list):
                block = next(
                    (w for w in workflows if w.get("name") == workflow_name), None,
                )
            else:
                block = workflows.get(workflow_name) if isinstance(workflows, dict) else None
            if block and "artifact_destination" in block:
                del block["artifact_destination"]
        self._mutate(_apply)

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

    def mutate(self, fn: Any) -> Any:
        """Public locked read-modify-write. `fn(data)` mutates in place; its return
        value (if any) is returned. Use this from route handlers instead of
        _read()/_write() so concurrent updates are not lost."""
        return self._mutate(fn)

    def _mutate(self, fn: Any) -> Any:
        """Locked read → mutate → write, returning the fn result."""
        with self._file_lock():
            data = self._read()
            result = fn(data)
            self._write(data)
        return result

    # ── file lock ────────────────────────────────────────────────

    SCHEMA_VERSION = 3  # bumped by each migration-bearing PR

    @contextmanager
    def _file_lock(self):
        """Exclusive cross-process lock around read-modify-write.

        Best-effort: a no-op on platforms without ``fcntl`` (Windows). The file
        backend is single-host by contract; multi-process durability is the
        Postgres roadmap path.
        """
        if fcntl is None:
            yield
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def run_migrations(self) -> None:
        """Versioned, backed-up, locked migration. Run once before serving."""
        with self._file_lock():
            data = self._read()
            current = int(data.get("schema_version", 0))
            if current >= self.SCHEMA_VERSION:
                return
            backup: Path | None = None
            if self._path.exists():
                stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
                backup = self._path.with_name(
                    f"{self._path.name}.bak-v{current}-{stamp}-{secrets.token_hex(3)}"
                )
                shutil.copy2(self._path, backup)
            try:
                for step in _MIGRATION_STEPS[current:self.SCHEMA_VERSION]:
                    step(data)
                data["schema_version"] = self.SCHEMA_VERSION
                self._write(data)
            except Exception:
                if backup is not None:
                    shutil.copy2(backup, self._path)  # restore pre-migration file
                raise

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

        # v1 → v2: back-fill `kind: inference` on every connection
        # record. The vault widened from inference-only to a generic
        # connections store (tools live here too) — old records all
        # belong to the original inference namespace.
        for rec in data.get("providers", []):
            rec.setdefault("kind", "inference")

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

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """Return the project dict for ``project_id`` or None.

        Handles both list-of-dicts (matching on ``slug`` or ``id``) and
        dict-keyed-by-slug shapes for forward-compatibility.
        """
        state = self._read()
        projects = state.get("projects", [])
        if isinstance(projects, dict):
            return projects.get(project_id)
        if isinstance(projects, list):
            for p in projects:
                if p.get("slug") == project_id or p.get("id") == project_id:
                    return p
        return None

    # ── project scoping helper ───────────────────────────────────

    @staticmethod
    def _filter_by_project(
        items: list[dict[str, Any]], project_id: str | None
    ) -> list[dict[str, Any]]:
        """Filter items by project scope.

        - project_id=None        → return ALL items (legacy single-org view)
        - project_id=SHARED_ONLY  → only org-shared rows (project_id NULL/empty)
        - project_id="X"          → rows with project_id=X OR org-shared (inherited)
        """
        if project_id is None:
            return items
        if project_id == SHARED_ONLY:
            return [i for i in items if i.get("project_id") in (None, "")]
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
        spec["project_id"] = project_id
        name = spec.get("name", "")

        def _apply(data: dict[str, Any]) -> None:
            agents = data.setdefault("agents", [])
            data["agents"] = [
                a for a in agents
                if not (a.get("name") == name and a.get("project_id") == project_id)
            ]
            data["agents"].append(spec)

        self._mutate(_apply)
        return spec

    def get_agent(self, name: str, project_id: str | None = None) -> dict[str, Any] | None:
        """Return the agent dict for ``name`` or None if not present.

        When ``project_id`` is given, prefers a scoped match; falls back to
        org-global records (``project_id`` is None/empty).  When
        ``project_id`` is None the first matching record is returned
        regardless of its project scope (org-global view, backward-compat).

        Handles both list-of-dicts (matching on ``name``) and
        dict-keyed-by-name shapes for forward-compatibility.
        """
        agents = self._read().get("agents", [])
        if isinstance(agents, dict):           # forward-compat dict shape
            agents = list(agents.values())
        matches = [a for a in agents if isinstance(a, dict) and a.get("name") == name]
        if project_id is None:
            return matches[0] if matches else None
        scoped = [a for a in matches if a.get("project_id") == project_id]
        if scoped:
            return scoped[0]
        glob = [a for a in matches if a.get("project_id") in (None, "")]
        return glob[0] if glob else None

    def delete_agent(self, name: str, project_id: str | None = None) -> bool:
        def _apply(data: dict[str, Any]) -> bool:
            agents = data.get("agents", [])
            before = len(agents)
            if project_id is None:
                data["agents"] = [a for a in agents if a.get("name") != name]
            elif project_id == SHARED_ONLY:
                data["agents"] = [
                    a for a in agents
                    if not (a.get("name") == name and a.get("project_id") in (None, ""))
                ]
            else:
                data["agents"] = [
                    a for a in agents
                    if not (a.get("name") == name and a.get("project_id") == project_id)
                ]
            return len(data["agents"]) != before

        return self._mutate(_apply)

    def rename_agent(self, old_name: str, new_name: str, project_id: str | None = None) -> dict[str, Any] | None:
        def _apply(data: dict[str, Any]) -> dict[str, Any] | None:
            for a in data.get("agents", []):
                if a.get("name") == old_name and (project_id is None or a.get("project_id") == project_id):
                    a["name"] = new_name
                    return a
            return None
        return self._mutate(_apply)

    # ── agent runs (individual agent executions) ────────────────
    #
    # Stored as data["agent_runs"]. One row per agent execution — both
    # standalone playground runs and inline-agent steps from workflow
    # runs. Each row carries a run_type ("standalone" | "workflow_step")
    # and, for workflow steps, the parent_workflow_run_id so that the
    # admin UI can link back to the workflow history.

    _AGENT_RUNS_MAX = 500

    def save_agent_run(self, run: dict[str, Any]) -> dict[str, Any]:
        """Persist an agent run record. Truncates to the most recent 500."""
        data = self._read()
        runs = data.setdefault("agent_runs", [])
        runs.insert(0, run)
        data["agent_runs"] = runs[: self._AGENT_RUNS_MAX]
        self._write(data)
        return run

    def list_agent_runs(
        self,
        *,
        project_id: str | None = None,
        agent_name: str | None = None,
        status: str | None = None,
        run_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        data = self._read()
        runs = data.get("agent_runs", [])
        runs = self._filter_by_project(runs, project_id)
        if agent_name:
            runs = [r for r in runs if r.get("agent_name") == agent_name]
        if status:
            runs = [r for r in runs if r.get("status") == status]
        if run_type:
            runs = [r for r in runs if r.get("run_type") == run_type]
        return runs[offset : offset + limit]

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        data = self._read()
        for r in data.get("agent_runs", []):
            if r.get("run_id") == run_id:
                return r
        return None

    # ── providers ────────────────────────────────────────────────

    def _load_providers(self, project_id: str | None) -> list[dict[str, Any]]:
        """Read + migrate + project-filter + env-var-enrich the raw provider list."""
        data = self._read()
        self._migrate(data)
        providers = self._filter_by_project(data.get("providers", []), project_id)
        for p in providers:
            env_key = p.get("env_var_key", "")
            if env_key:
                p["env_var_set"] = bool(os.environ.get(env_key))
        return providers

    def list_providers(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [_redact_provider(p) for p in self._load_providers(project_id)]

    def upsert_provider(self, provider: dict[str, Any]) -> dict[str, Any]:
        provider = copy.deepcopy(provider)
        data = self._read()
        self._migrate(data)
        providers = data.setdefault("providers", [])
        pname = provider.get("provider_name", "")
        # Auto-generate ID and env_var_key (project-scoped so same-name providers
        # in different projects get distinct IDs and don't clobber each other)
        if not provider.get("id"):
            provider["id"] = f"prov-{provider.get('project_id') or 'global'}-{pname}"
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
        provider.setdefault("default", False)
        # If this provider is being upserted as the default, clear the
        # flag on all other providers in the same project scope so there
        # is at most one default per scope at any time.
        if provider.get("default"):
            scope = provider.get("project_id")
            for other in providers:
                if other.get("provider_name") == pname:
                    continue
                if other.get("project_id") == scope:
                    other["default"] = False
        # Encrypt plaintext secrets before persisting
        if _provider_has_plaintext_secret(provider):
            from sagewai.admin.secret_crypto import get_admin_crypto
            _encrypt_provider(provider, get_admin_crypto())
        # Upsert by (provider_name, project_id) so same-name providers in
        # different projects coexist without clobbering each other.
        for i, existing in enumerate(providers):
            if existing.get("provider_name") == pname and existing.get("project_id") == provider.get("project_id"):
                providers[i] = provider
                self._write(data)
                return provider
        providers.append(provider)
        self._write(data)
        return provider

    def set_default_provider(
        self,
        provider_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark *provider_id* as the default (one default per project scope).

        Looks the provider up by ``id`` first, then by ``provider_name``,
        within the given *project_id* scope. Returns the updated record,
        or ``None`` if no matching provider exists.
        """
        # Org-shared scope is stored as project_id None; normalize the sentinel so
        # the lookup AND the default-flag update below operate on the same rows
        # (otherwise an org-shared default lookup succeeds but never sets default).
        if project_id == SHARED_ONLY:
            project_id = None
        data = self._read()
        self._migrate(data)
        providers = data.get("providers", [])
        target = next(
            (
                p
                for p in providers
                if (p.get("id") == provider_id or p.get("provider_name") == provider_id)
                and p.get("project_id") == project_id
            ),
            None,
        )
        if target is None:
            return None
        for p in providers:
            if p.get("project_id") == project_id:
                p["default"] = (p is target)
        self._write(data)
        return target

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

    def list_providers_decrypted(
        self, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Internal-only: full records with secrets decrypted. NEVER return over HTTP."""
        providers = self._load_providers(project_id)
        if not _any_encrypted_provider(providers):
            return providers  # nothing encrypted → no need for a key
        from sagewai.admin.secret_crypto import AdminKeyMissing, get_admin_crypto
        from sagewai.sealed.master_key import MasterKeyMissing
        try:
            crypto = get_admin_crypto()
        except (AdminKeyMissing, MasterKeyMissing):
            crypto = None  # null secrets, never expose ciphertext as a credential
        return [_decrypt_provider(p, crypto) for p in providers]

    def get_provider_decrypted(
        self, name_or_id: str, project_id: str | None = None
    ) -> dict[str, Any] | None:
        """Return a provider record with secrets decrypted.

        For internal use only (inference path, connection test).  Never
        return this over the wire — use :meth:`list_providers` for API
        responses (redacted shape).
        """
        for p in self._load_providers(project_id):
            if p.get("id") == name_or_id or p.get("provider_name") == name_or_id:
                if not _provider_is_encrypted(p):
                    return p
                from sagewai.admin.secret_crypto import AdminKeyMissing, get_admin_crypto
                from sagewai.sealed.master_key import MasterKeyMissing
                try:
                    crypto = get_admin_crypto()
                except (AdminKeyMissing, MasterKeyMissing):
                    crypto = None  # null secrets, never expose ciphertext as a credential
                return _decrypt_provider(p, crypto)
        return None

    def require_secret_key_if_encrypted(self) -> None:
        """Fail closed at startup: if encrypted provider secrets exist, the master
        key must resolve (raises AdminKeyMissing in a keyless container)."""
        if _any_encrypted_provider(self._read().get("providers", [])):
            from sagewai.admin.secret_crypto import get_admin_crypto
            get_admin_crypto()  # raises in a container with no key

    # ── auth ─────────────────────────────────────────────────────

    def get_or_create_csrf_secret(self) -> str:
        """Stable server-only secret for CSRF token signing.

        Generated once and persisted; never returned over the wire (unlike the
        admin id, which appears in login/account responses).
        """
        secret = self._read().get("csrf_secret")
        if secret:
            return secret

        def _apply(d: dict[str, Any]) -> str:
            if d.get("csrf_secret"):
                return d["csrf_secret"]
            d["csrf_secret"] = secrets.token_hex(32)
            return d["csrf_secret"]

        return self._mutate(_apply)

    def validate_login(self, email: str, password: str) -> dict[str, Any] | None:
        """Validate credentials. Returns user info + raw token, or None."""
        data = self._read()
        admin = data.get("admin")
        if not admin or admin.get("email") != email:
            return None
        if not _verify_password(password, admin["password_hash"], admin["password_salt"]):
            return None
        raw = _make_token()
        with self._file_lock():
            data = self._read()
            tokens = data.setdefault("active_tokens", [])
            tokens.append(_session_record(raw))
            data["active_tokens"] = tokens[-_MAX_TOKENS:]
            self._write(data)
        return {
            "access_token": raw,
            "token_type": "bearer",
            "user": {
                "id": admin["id"],
                "email": admin["email"],
                "display_name": admin.get("name", ""),
                "avatar_url": None,
                "role": admin.get("role", "admin"),
            },
        }

    def _find_session(self, data: dict[str, Any], raw: str) -> dict[str, Any] | None:
        h = _hash_token(raw)
        for rec in data.get("active_tokens", []):
            if isinstance(rec, dict) and hmac.compare_digest(rec.get("hash", ""), h):
                return rec
            # Legacy plaintext record (pre-v1 migration). run_migrations() is
            # invoked at app startup (create_admin_serve_app), so this path is
            # transient; constant-time compare is defense-in-depth.
            if isinstance(rec, str) and hmac.compare_digest(rec, raw):
                # No expiry on legacy tokens until migrated (intentional policy).
                return {"hash": h, "expires_at": None}
        return None

    def validate_token(self, token: str) -> bool:
        rec = self._find_session(self._read(), token)
        return bool(rec and _record_is_live(rec))

    def get_user_by_token(self, token: str) -> dict[str, Any] | None:
        data = self._read()
        rec = self._find_session(data, token)
        if not rec or not _record_is_live(rec):
            return None
        admin = data.get("admin", {})
        return {
            "id": admin.get("id", ""),
            "email": admin.get("email", ""),
            "display_name": admin.get("name", ""),
            "avatar_url": None,
            "role": admin.get("role", "admin"),
        }

    def get_admin_user(self) -> dict[str, Any] | None:
        """Return the (single) admin user dict, or None if setup is incomplete.

        The current-user view for /auth/me and /api/v1/account, resolved from the
        authenticated principal (set by AuthMiddleware) rather than by re-looking
        up a session token — so API-token callers are treated consistently.
        """
        admin = self._read().get("admin")
        if not admin:
            return None
        return {
            "id": admin.get("id", ""),
            "email": admin.get("email", ""),
            "display_name": admin.get("name", ""),
            "avatar_url": None,
            "role": admin.get("role", "admin"),
        }

    def refresh_token(self, old_token: str) -> dict[str, Any] | None:
        """Rotate a token: append a NEW token and return it.

        The old token intentionally stays valid until its own TTL expires — this
        overlap supports multiple concurrent browsers and the e2e suite, which
        fires many refreshes against a shared storageState. The list is bounded
        by _MAX_TOKENS. Use revoke_session_token()/logout to hard-invalidate.
        """
        with self._file_lock():
            data = self._read()
            rec = self._find_session(data, old_token)
            if not rec or not _record_is_live(rec):
                return None
            raw = _make_token()
            tokens = data.get("active_tokens", [])
            tokens.append(_session_record(raw))
            data["active_tokens"] = tokens[-_MAX_TOKENS:]
            self._write(data)
        admin = data.get("admin", {})
        return {
            "access_token": raw,
            "token_type": "bearer",
            "user": {
                "id": admin.get("id", ""),
                "email": admin.get("email", ""),
                "display_name": admin.get("name", ""),
                "avatar_url": None,
                "role": admin.get("role", "admin"),
            },
        }

    def revoke_session_token(self, token: str) -> bool:
        h = _hash_token(token)

        def _apply(data: dict[str, Any]) -> bool:
            before = len(data.get("active_tokens", []))
            data["active_tokens"] = [
                r for r in data.get("active_tokens", [])
                if not (isinstance(r, dict) and r.get("hash") == h)
                and not (isinstance(r, str) and r == token)
            ]
            return len(data["active_tokens"]) != before

        return self._mutate(_apply)

    # ── API tokens ───────────────────────────────────────────────

    def create_api_token(self, *, name: str, scopes: list[str]) -> dict[str, Any]:
        raw = f"sw_{secrets.token_urlsafe(32)}"
        entry = {
            "id": f"tok-{secrets.token_hex(6)}",
            "name": name or "Unnamed",
            "token_hash": _hash_token(raw),
            "prefix": raw[:12],
            "scopes": scopes or ["read"],
            "created_at": _now_iso(),
            "last_used": None,
            "revoked": False,
        }
        self._mutate(lambda d: d.setdefault("api_tokens", []).append(entry))
        return {**{k: v for k, v in entry.items() if k != "token_hash"}, "token": raw}

    def list_api_tokens(self) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in t.items() if k not in ("token_hash", "token")}
            for t in self._read().get("api_tokens", [])
        ]

    def find_api_token(self, raw: str) -> dict[str, Any] | None:
        h = _hash_token(raw)
        for t in self._read().get("api_tokens", []):
            if t.get("token_hash") and hmac.compare_digest(t.get("token_hash", ""), h) and not t.get("revoked"):
                return {k: v for k, v in t.items() if k not in ("token_hash", "token")}
        return None

    def revoke_api_token(self, token_id: str) -> bool:
        def _apply(d: dict[str, Any]) -> bool:
            for t in d.get("api_tokens", []):
                if t.get("id") == token_id:
                    t["revoked"] = True
                    return True
            return False
        return self._mutate(_apply)

    def issue_session(self) -> dict[str, Any] | None:
        """Issue a session for the configured admin WITHOUT a password check.

        Loopback dev-trust bootstrap only. Stores only the hashed token; returns
        the same payload shape as validate_login.
        """
        admin = self._read().get("admin")
        if not admin:
            return None
        raw = _make_token()
        with self._file_lock():
            data = self._read()
            tokens = data.setdefault("active_tokens", [])
            tokens.append(_session_record(raw))
            data["active_tokens"] = tokens[-_MAX_TOKENS:]
            self._write(data)
        return {
            "access_token": raw,
            "token_type": "bearer",
            "user": {
                "id": admin.get("id", ""),
                "email": admin.get("email", ""),
                "display_name": admin.get("name", ""),
                "avatar_url": None,
                "role": admin.get("role", "admin"),
            },
        }

    def delete_api_token(self, token_id: str) -> bool:
        def _apply(d: dict[str, Any]) -> bool:
            before = len(d.get("api_tokens", []))
            d["api_tokens"] = [t for t in d.get("api_tokens", []) if t.get("id") != token_id]
            return len(d["api_tokens"]) != before
        return self._mutate(_apply)

    def update_admin_profile(self, *, display_name: str | None) -> dict[str, Any]:
        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            admin = data.setdefault("admin", {})
            if display_name:
                admin["name"] = display_name
            return {
                "id": admin.get("id", ""),
                "email": admin.get("email", ""),
                "display_name": admin.get("name", ""),
                "avatar_url": None,
            }
        return self._mutate(_apply)

    def change_admin_password(self, current_password: str, new_password: str) -> bool:
        """Verify the current password and set a new one, atomically under the lock.

        Returns False if the current password is incorrect.
        """
        with self._file_lock():
            data = self._read()
            admin = data.get("admin", {})
            if not _verify_password(current_password, admin.get("password_hash", ""),
                                    admin.get("password_salt", "")):
                return False
            new_hash, new_salt = _hash_password(new_password)
            admin["password_hash"] = new_hash
            admin["password_salt"] = new_salt
            self._write(data)
            return True

    def reset(self) -> None:
        """Delete the state file (for testing)."""
        if self._path.exists():
            self._path.unlink()


# ── migration steps registry ─────────────────────────────────────────
# Index ``i`` transforms schema ``vi → v(i+1)``.


def _migrate_v0_to_v1(data: dict[str, Any]) -> None:
    """Hash legacy plaintext session + API tokens."""
    sessions = data.get("active_tokens", [])
    data["active_tokens"] = [
        _session_record(t) if isinstance(t, str) else t for t in sessions
    ]
    for rec in data.get("api_tokens", []):
        if "token" in rec:
            rec["token_hash"] = _hash_token(rec.pop("token"))


def _migrate_v1_to_v2(data: dict[str, Any]) -> None:
    """Encrypt plaintext provider secrets at rest (idempotent via the fernet: marker)."""
    providers = data.get("providers", [])
    if not any(_provider_has_plaintext_secret(p) for p in providers):
        return
    from sagewai.admin.secret_crypto import get_admin_crypto
    crypto = get_admin_crypto()
    for p in providers:
        _encrypt_provider(p, crypto)


def _migrate_v2_to_v3(data: dict[str, Any]) -> None:
    """Project-scope keying is enforced going forward. Legacy records keep their
    existing project_id (None == org-global, still visible via _filter_by_project),
    so no data transform is needed — this step only advances the schema version."""
    # intentionally a no-op


_MIGRATION_STEPS = [_migrate_v0_to_v1, _migrate_v1_to_v2, _migrate_v2_to_v3]
