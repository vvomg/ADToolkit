"""
api/config_history.py — История применения конфигурационных профилей.

GET    /api/config/history            → {history: HistoryEntry[], total: int}
GET    /api/config/history/{id}       → HistoryEntry
DELETE /api/config/history/{id}       → {deleted: true}
POST   /api/config/history            → создать запись (internal, используется из profiles.py)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models.db_models import ConfigApplyHistory, get_db_engine, init_db

router = APIRouter(prefix="/api/config/history", tags=["config-history"])


# ─── DB session ──────────────────────────────────────────────────────────────

def _get_session() -> Session:
    engine = get_db_engine(settings.get_database_sync_url())
    init_db(engine)
    return Session(engine)


# ─── Pydantic schemas ─────────────────────────────────────────────────────────

class HistoryEntry(BaseModel):
    id: int
    applied_at: datetime
    profile_slug: str
    profile_name: str
    apply_mode: str          # 'cmd' | 'ansible'
    playbook_path: Optional[str] = None
    target_hosts: list[str]
    modules_applied: list[str]
    node_versions: Optional[dict[str, str]] = None
    status: str              # 'ok' | 'partial' | 'failed'
    errors: Optional[list[str]] = None

    class Config:
        from_attributes = True


class CreateHistoryRequest(BaseModel):
    profile_slug: str
    profile_name: str
    apply_mode: str
    playbook_path: Optional[str] = None
    target_hosts: list[str]
    modules_applied: list[str]
    node_versions: Optional[dict[str, str]] = None
    status: str = "ok"
    errors: Optional[list[str]] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _row_to_entry(row: ConfigApplyHistory) -> HistoryEntry:
    return HistoryEntry(
        id=row.id,
        applied_at=row.applied_at,
        profile_slug=row.profile_slug,
        profile_name=row.profile_name,
        apply_mode=row.apply_mode,
        playbook_path=row.playbook_path,
        target_hosts=row.target_hosts or [],
        modules_applied=row.modules_applied or [],
        node_versions=row.node_versions,
        status=row.status,
        errors=row.errors,
    )


# ─── Service function (used by profiles.py directly) ─────────────────────────

def create_history_entry(req: CreateHistoryRequest) -> HistoryEntry:
    """Создать запись в истории. Вызывается из apply/stream после завершения."""
    db = _get_session()
    try:
        row = ConfigApplyHistory(
            applied_at=datetime.utcnow(),
            profile_slug=req.profile_slug,
            profile_name=req.profile_name,
            apply_mode=req.apply_mode,
            playbook_path=req.playbook_path,
            target_hosts=req.target_hosts,
            modules_applied=req.modules_applied,
            node_versions=req.node_versions,
            status=req.status,
            errors=req.errors,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _row_to_entry(row)
    finally:
        db.close()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def list_history(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    profile_slug: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Список записей истории применений (от новых к старым)."""
    db = _get_session()
    try:
        q = db.query(ConfigApplyHistory).order_by(ConfigApplyHistory.applied_at.desc())
        if profile_slug:
            q = q.filter(ConfigApplyHistory.profile_slug == profile_slug)
        if status:
            q = q.filter(ConfigApplyHistory.status == status)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        return {
            "history": [_row_to_entry(r).model_dump() for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.get("/{entry_id}")
async def get_history_entry(entry_id: int) -> dict[str, Any]:
    """Получить одну запись истории по ID."""
    db = _get_session()
    try:
        row = db.query(ConfigApplyHistory).filter(ConfigApplyHistory.id == entry_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"History entry {entry_id} not found")
        return {"entry": _row_to_entry(row).model_dump()}
    finally:
        db.close()


@router.delete("/{entry_id}")
async def delete_history_entry(entry_id: int) -> dict[str, Any]:
    """Удалить запись из истории."""
    db = _get_session()
    try:
        row = db.query(ConfigApplyHistory).filter(ConfigApplyHistory.id == entry_id).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"History entry {entry_id} not found")
        db.delete(row)
        db.commit()
        return {"deleted": True}
    finally:
        db.close()


@router.post("")
async def create_history(req: CreateHistoryRequest) -> dict[str, Any]:
    """Создать запись истории (публичный эндпоинт, если нужно)."""
    entry = create_history_entry(req)
    return {"entry": entry.model_dump()}
