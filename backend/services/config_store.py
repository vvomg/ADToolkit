"""
config_store.py — YAML-based configuration store service.

Variant B: read from live CMD → save to YAML files in config-store/ →
apply via Ansible. NO direct CMD writes from here.

Directory layout:
  iva-mail-ansible/config-store/
    modules/
      <ip>/
        <module_name>.yaml
    domains/
      <domain>/
        _domain.yaml
        objects/
          <uid>.yaml
    schema.yaml
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _config_store_root() -> Path:
    """Return path to config-store/ relative to this file's repo root."""
    # backend/services/config_store.py → backend/ → project root → iva-mail-ansible/
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "iva-mail-ansible" / "config-store"


def modules_dir(ip: str) -> Path:
    return _config_store_root() / "modules" / ip


def domains_dir(domain: str) -> Path:
    return _config_store_root() / "domains" / domain


def objects_dir(domain: str) -> Path:
    return domains_dir(domain) / "objects"


def schema_path() -> Path:
    return _config_store_root() / "schema.yaml"


# ---------------------------------------------------------------------------
# Low-level YAML helpers
# ---------------------------------------------------------------------------

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict[str, Any], meta: dict[str, Any] | None = None) -> None:
    _ensure_dir(path.parent)
    payload: dict[str, Any] = {}
    if meta:
        payload["_meta"] = meta
    payload.update(data)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CMD response parser — simple key=value / JSON blob detection
# ---------------------------------------------------------------------------

def parse_cmd_config(raw_lines: list[str]) -> dict[str, Any]:
    """
    Parse CMD ModuleReadConfig/DomainReadConfig/ObjectReadConfig response lines.
    Lines are typically:   KEY=VALUE
    or a JSON-encoded blob returned by some fields.
    Returns a flat dict.
    """
    result: dict[str, Any] = {}
    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith("200") or line.startswith("4") or line.startswith("5"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Try JSON decode for complex values
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                result[key] = value
        else:
            # Treat as a continuation or ignored header
            pass
    return result


# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------

async def save_module_config(
    ip: str,
    module_name: str,
    config_data: dict[str, Any],
    source: str = "live_read",
) -> Path:
    """Persist a module config snapshot to disk."""
    path = modules_dir(ip) / f"{module_name}.yaml"
    meta = {
        "ip": ip,
        "module": module_name,
        "source": source,
        "saved_at": _now_iso(),
    }
    _save_yaml(path, config_data, meta=meta)
    logger.info("Saved module config %s/%s → %s", ip, module_name, path)
    return path


def load_module_config(ip: str, module_name: str) -> dict[str, Any]:
    path = modules_dir(ip) / f"{module_name}.yaml"
    return _load_yaml(path)


def list_module_configs(ip: str) -> list[str]:
    """Return list of saved module names for an IP."""
    d = modules_dir(ip)
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.yaml")]


def list_all_node_ips() -> list[str]:
    """Return IPs that have at least one module config saved."""
    modules_root = _config_store_root() / "modules"
    if not modules_root.exists():
        return []
    return [d.name for d in modules_root.iterdir() if d.is_dir()]


# ---------------------------------------------------------------------------
# Domain-level config
# ---------------------------------------------------------------------------

async def save_domain_config(
    domain: str,
    config_data: dict[str, Any],
    source: str = "live_read",
) -> Path:
    path = domains_dir(domain) / "_domain.yaml"
    meta = {
        "domain": domain,
        "source": source,
        "saved_at": _now_iso(),
    }
    _save_yaml(path, config_data, meta=meta)
    logger.info("Saved domain config %s → %s", domain, path)
    return path


def load_domain_config(domain: str) -> dict[str, Any]:
    path = domains_dir(domain) / "_domain.yaml"
    return _load_yaml(path)


def list_saved_domains() -> list[str]:
    domains_root = _config_store_root() / "domains"
    if not domains_root.exists():
        return []
    return [d.name for d in domains_root.iterdir() if d.is_dir()]


# ---------------------------------------------------------------------------
# Object-level config
# ---------------------------------------------------------------------------

async def save_object_config(
    domain: str,
    uid: str,
    config_data: dict[str, Any],
    source: str = "live_read",
) -> Path:
    safe_uid = uid.replace("/", "_").replace("@", "_at_")
    path = objects_dir(domain) / f"{safe_uid}.yaml"
    meta = {
        "domain": domain,
        "uid": uid,
        "source": source,
        "saved_at": _now_iso(),
    }
    _save_yaml(path, config_data, meta=meta)
    return path


def load_object_config(domain: str, uid: str) -> dict[str, Any]:
    safe_uid = uid.replace("/", "_").replace("@", "_at_")
    path = objects_dir(domain) / f"{safe_uid}.yaml"
    return _load_yaml(path)


def list_saved_objects(domain: str) -> list[str]:
    d = objects_dir(domain)
    if not d.exists():
        return []
    data = []
    for p in d.glob("*.yaml"):
        raw = _load_yaml(p)
        meta = raw.get("_meta", {})
        data.append(meta.get("uid", p.stem))
    return data


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

async def save_schema(schema_data: dict[str, Any]) -> Path:
    path = schema_path()
    meta = {"source": "live_read", "saved_at": _now_iso()}
    _save_yaml(path, schema_data, meta=meta)
    return path


def load_schema() -> dict[str, Any]:
    return _load_yaml(schema_path())


# ---------------------------------------------------------------------------
# Bulk dump helper (used by dump playbook post-processing)
# ---------------------------------------------------------------------------

async def bulk_save_module_configs(
    ip: str,
    configs_by_module: dict[str, dict[str, Any]],
    source: str = "ansible_dump",
) -> list[Path]:
    saved = []
    for module_name, data in configs_by_module.items():
        p = await save_module_config(ip, module_name, data, source=source)
        saved.append(p)
    return saved


# ---------------------------------------------------------------------------
# Diff helper — compare stored vs fresh live data
# ---------------------------------------------------------------------------

def diff_configs(stored: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Return structured diff: added / removed / changed keys."""
    stored_clean = {k: v for k, v in stored.items() if not k.startswith("_")}
    live_clean = {k: v for k, v in live.items() if not k.startswith("_")}

    added = {k: live_clean[k] for k in live_clean if k not in stored_clean}
    removed = {k: stored_clean[k] for k in stored_clean if k not in live_clean}
    changed = {
        k: {"stored": stored_clean[k], "live": live_clean[k]}
        for k in stored_clean
        if k in live_clean and stored_clean[k] != live_clean[k]
    }
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "identical": not (added or removed or changed),
    }
