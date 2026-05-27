"""
profile_store.py — YAML-based Configuration Profiles service.

Each profile is stored as a single YAML file:
  {CONFIG_STORE_DIR}/profiles/{slug}.yaml

YAML structure:
  name: str
  slug: str
  created_at: ISO string
  updated_at: ISO string
  notes: str
  modules:
    Cluster: {BackendList: [...], Port: 106, ...}
    SMTP: {...}
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException
from pydantic import BaseModel

from . import git_service as git

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ProfileMeta(BaseModel):
    name: str
    slug: str
    created_at: str
    updated_at: str
    notes: str
    module_names: list[str]


class ProfileFull(BaseModel):
    name: str
    slug: str
    created_at: str
    updated_at: str
    notes: str
    modules: dict[str, Any]
    module_names: list[str] = []


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def profiles_dir() -> Path:
    """Return path to profiles/ directory inside CONFIG_STORE_DIR."""
    base = Path(os.environ.get("CONFIG_STORE_DIR", "/opt/ivamail-config-store"))
    p = base / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _profile_path(slug: str) -> Path:
    return profiles_dir() / f"{slug}.yaml"


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Convert 'My Profile' → 'my-profile'."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-") or "profile"


def _unique_slug(base_slug: str) -> str:
    """Return base_slug, or base_slug-2, base_slug-3, ... if file already exists."""
    if not _profile_path(base_slug).exists():
        return base_slug
    i = 2
    while _profile_path(f"{base_slug}-{i}").exists():
        i += 1
    return f"{base_slug}-{i}"


# ---------------------------------------------------------------------------
# Low-level YAML helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_raw(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _raw_to_full(raw: dict[str, Any]) -> ProfileFull:
    modules = raw.get("modules") or {}
    return ProfileFull(
        name=raw.get("name", ""),
        slug=raw.get("slug", ""),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
        notes=raw.get("notes", ""),
        modules=modules,
        module_names=list(modules.keys()),
    )


def _raw_to_meta(raw: dict[str, Any]) -> ProfileMeta:
    return ProfileMeta(
        name=raw.get("name", ""),
        slug=raw.get("slug", ""),
        created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
        notes=raw.get("notes", ""),
        module_names=list((raw.get("modules") or {}).keys()),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_profiles() -> list[ProfileMeta]:
    """Return all profiles as lightweight ProfileMeta (no modules content)."""
    result = []
    for p in sorted(profiles_dir().glob("*.yaml")):
        raw = _load_raw(p)
        if raw:
            result.append(_raw_to_meta(raw))
    return result


def get_profile(slug: str) -> ProfileFull:
    """Return full profile. Raises HTTPException 404 if not found."""
    path = _profile_path(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")
    raw = _load_raw(path)
    return _raw_to_full(raw)


def create_profile(name: str, notes: str = "") -> ProfileFull:
    """Create a new empty profile. Returns ProfileFull."""
    slug = _unique_slug(slugify(name))
    now = _now_iso()
    raw: dict[str, Any] = {
        "name": name,
        "slug": slug,
        "created_at": now,
        "updated_at": now,
        "notes": notes,
        "modules": {},
    }
    _save_raw(_profile_path(slug), raw)
    logger.info("Created profile '%s' → %s", name, slug)
    return _raw_to_full(raw)


def update_profile(
    slug: str,
    name: str | None = None,
    notes: str | None = None,
) -> ProfileFull:
    """
    Update profile metadata.
    If name changes, the underlying file is renamed to the new slug.
    """
    path = _profile_path(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")

    raw = _load_raw(path)
    new_slug = slug

    if name is not None and name != raw.get("name"):
        candidate = slugify(name)
        # Allow same slug if only casing/punctuation changed and no collision
        if candidate != slug:
            new_slug = _unique_slug(candidate)
        raw["name"] = name
        raw["slug"] = new_slug

    if notes is not None:
        raw["notes"] = notes

    raw["updated_at"] = _now_iso()

    if new_slug != slug:
        # Rename file
        new_path = _profile_path(new_slug)
        _save_raw(new_path, raw)
        path.unlink()
        logger.info("Renamed profile '%s' → '%s'", slug, new_slug)
    else:
        _save_raw(path, raw)

    return _raw_to_full(raw)


def delete_profile(slug: str) -> None:
    """Delete profile file. Raises HTTPException 404 if not found."""
    path = _profile_path(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")
    path.unlink()
    logger.info("Deleted profile '%s'", slug)


def upsert_module(slug: str, module: str, config: dict[str, Any]) -> ProfileFull:
    """Add or replace a module config inside a profile."""
    path = _profile_path(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")

    raw = _load_raw(path)
    modules = raw.get("modules") or {}
    modules[module] = config
    raw["modules"] = modules
    raw["updated_at"] = _now_iso()
    _save_raw(path, raw)
    logger.info("Upserted module '%s' into profile '%s'", module, slug)
    return _raw_to_full(raw)


def remove_module(slug: str, module: str) -> ProfileFull:
    """Remove a module from a profile. Raises 404 if profile or module not found."""
    path = _profile_path(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")

    raw = _load_raw(path)
    modules = raw.get("modules") or {}
    if module not in modules:
        raise HTTPException(
            status_code=404,
            detail=f"Module '{module}' not found in profile '{slug}'",
        )
    del modules[module]
    raw["modules"] = modules
    raw["updated_at"] = _now_iso()
    _save_raw(path, raw)
    logger.info("Removed module '%s' from profile '%s'", module, slug)
    return _raw_to_full(raw)


def duplicate_profile(slug: str, new_name: str) -> ProfileFull:
    """Duplicate an existing profile under a new name."""
    path = _profile_path(slug)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile '{slug}' not found")

    raw = _load_raw(path)
    new_slug = _unique_slug(slugify(new_name))
    now = _now_iso()
    new_raw: dict[str, Any] = {
        **raw,
        "name": new_name,
        "slug": new_slug,
        "created_at": now,
        "updated_at": now,
    }
    _save_raw(_profile_path(new_slug), new_raw)
    logger.info("Duplicated profile '%s' → '%s'", slug, new_slug)
    return _raw_to_full(new_raw)


# ---------------------------------------------------------------------------
# Git auto-commit helper (mirrors git_service pattern)
# ---------------------------------------------------------------------------

async def _git_commit(message: str) -> None:
    """Stage profiles/ directory and commit. Errors are logged, not raised."""
    try:
        result = await git.commit_config_changes(message)
        if not result.get("ok"):
            logger.warning("git commit for profiles skipped/failed: %s", result.get("error") or result.get("message"))
    except Exception as exc:
        logger.warning("git commit for profiles raised: %s", exc)
