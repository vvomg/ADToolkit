"""
api/playbooks.py — Playbook CRUD, run and conversion API.

GET    /                      → PlaybookMeta[]
GET    /{name}                → {name, content, meta}
PUT    /{name}                → {name, updated}
DELETE /{name}                → {deleted}
POST   /{name}/run/stream     → SSE stream
POST   /{name}/to-profile     → {profile_slug, profile_name}
"""
from __future__ import annotations

import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services import ansible_runner as ar
from ..services import profile_store as ps

logger = logging.getLogger(__name__)
router = APIRouter()

CONFIG_STORE_DIR: str = os.environ.get("CONFIG_STORE_DIR", "/opt/ivamail-config-store")


def _gen_dir() -> Path:
    return Path(CONFIG_STORE_DIR) / "_generated"


def _safe_path(name: str) -> Path:
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise HTTPException(400, "Invalid playbook name")
    if len(name) > 255:
        raise HTTPException(400, "Playbook name too long")
    if Path(name).suffix not in (".yml", ".yaml"):
        raise HTTPException(400, "Must be .yml or .yaml file")
    return _gen_dir() / name


class PlaybookMeta(BaseModel):
    name: str
    path: str
    created_at: str
    size_bytes: int
    hosts: List[str]
    mode: str
    prefix: str


class PlaybookRunRequest(BaseModel):
    cmd_user: str = ""
    cmd_password: str = ""


class PlaybookUpdateRequest(BaseModel):
    content: str


def _parse_meta(p: Path) -> PlaybookMeta:
    hosts: List[str] = []
    mode = "unknown"
    try:
        with p.open(encoding="utf-8", errors="replace") as f:
            for _ in range(15):
                line = f.readline()
                if not line:
                    break
                m = re.match(r"#\s*Hosts:\s*(.+)", line)
                if m:
                    hosts = [h.strip() for h in m.group(1).split(",") if h.strip()]
                m2 = re.match(r"#\s*Mode:\s*(\S+)", line)
                if m2:
                    mode = m2.group(1)
    except Exception:
        pass
    stat = p.stat()
    name = p.name
    prefix_m = re.match(r"^(.+?)(?=-\d{8}T|-20\d{2})", name)
    prefix = prefix_m.group(1) if prefix_m else name.rsplit(".", 1)[0]
    return PlaybookMeta(
        name=name,
        path=str(p),
        created_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        size_bytes=stat.st_size,
        hosts=hosts,
        mode=mode,
        prefix=prefix,
    )


@router.get("/", response_model=List[PlaybookMeta])
def list_playbooks() -> List[PlaybookMeta]:
    d = _gen_dir()
    if not d.exists():
        return []
    files = sorted(
        [f for f in d.iterdir() if f.suffix in (".yml", ".yaml") and f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return [_parse_meta(f) for f in files]


@router.get("/{name}")
def get_playbook(name: str):
    p = _safe_path(name)
    if not p.exists():
        raise HTTPException(404, "Playbook not found")
    content = p.read_text(encoding="utf-8", errors="replace")
    return {"name": name, "content": content, "meta": _parse_meta(p)}


@router.put("/{name}")
def update_playbook(name: str, body: PlaybookUpdateRequest):
    p = _safe_path(name)
    if not p.exists():
        raise HTTPException(404, "Playbook not found")
    p.write_text(body.content, encoding="utf-8")
    return {"name": name, "updated": True}


@router.delete("/{name}")
def delete_playbook(name: str):
    p = _safe_path(name)
    if not p.exists():
        raise HTTPException(404, "Playbook not found")
    p.unlink()
    return {"deleted": True, "name": name}


@router.post("/{name}/run/stream")
async def run_playbook_stream(name: str, body: PlaybookRunRequest) -> StreamingResponse:
    p = _safe_path(name)
    if not p.exists():
        raise HTTPException(404, "Playbook not found")
    env: dict = {}
    if body.cmd_user:
        env["IVAMAIL_CMD_USER"] = body.cmd_user
    if body.cmd_password:
        env["IVAMAIL_CMD_PASSWORD"] = body.cmd_password
    path_str = str(p)

    async def _gen():
        try:
            async for line in ar.stream_playbook(path_str, env=env or None):
                yield f"data: {line}\n\n"
        except Exception as e:
            logger.exception("Playbook run error: %s", e)
            yield f"data: ERROR: {e}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{name}/to-profile")
def playbook_to_profile(name: str):
    p = _safe_path(name)
    if not p.exists():
        raise HTTPException(404, "Playbook not found")
    content = p.read_text(encoding="utf-8", errors="replace")
    modules: dict = {}
    for m in re.finditer(r"--module\s+(\S+)(.*?)(?=\n\s*-\s|\Z)", content, re.DOTALL):
        module_name = m.group(1)
        kvs = dict(re.findall(r"--kv\s+(\S+)=(\S+)", m.group(2)))
        if kvs:
            modules.setdefault(module_name, {}).update(kvs)
    if not modules:
        raise HTTPException(422, "No --kv module tasks found in playbook")
    profile = ps.create_profile(
        name=f"From {Path(name).stem}",
        notes=f"Auto-converted from playbook: {name}",
    )
    for mod, cfg in modules.items():
        ps.upsert_module(profile.slug, mod, cfg)
    return {"profile_slug": profile.slug, "profile_name": profile.name}
