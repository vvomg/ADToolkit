"""
Inventory API — запуск и отслеживание инвентаризации почтовых ящиков IVA Mail.

POST /api/inventory/start                    — запустить сканирование
GET  /api/inventory/                         — список всех сканирований
GET  /api/inventory/{scan_id}                — статус + лог конкретного скана
GET  /api/inventory/{scan_id}/download/json  — скачать JSON результат
GET  /api/inventory/{scan_id}/download/html  — открыть HTML отчёт
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..models.db_models import InventoryScan, Base, get_db_engine

logger = logging.getLogger(__name__)
router = APIRouter()

# Папка для хранения результатов сканирований
SCAN_OUTPUT_DIR = Path("./inventory_results")
SCAN_OUTPUT_DIR.mkdir(exist_ok=True)


# ── DB helper ──────────────────────────────────────────────────────────────────

def _get_session() -> Session:
    engine = get_db_engine("sqlite:///./ivamail_deploy.db")
    Base.metadata.create_all(engine)
    return Session(engine)


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class InventoryConfig(BaseModel):
    host: str
    port: int = 106
    user: str
    password: str
    all_domains: bool = True
    domains: List[str] = []
    accounts: List[str] = []
    include_imap_stats: bool = True
    imap_host: Optional[str] = None
    imap_port: Optional[int] = None
    imap_ssl: bool = False
    imap_user: Optional[str] = None
    imap_password: Optional[str] = None
    cmd_workers: int = 20
    imap_workers: int = 20
    timeout: int = 60
    page_size: int = 1000
    include_acl: bool = True
    include_non_mail_objects: bool = True
    recalculate_storage: bool = True
    mailbox_class: str = "mail"
    report_title: str = "IVA Mail Inventory"
    max_domains: Optional[int] = None
    max_accounts_per_domain: Optional[int] = None
    max_mailboxes_per_account: Optional[int] = None


class ScanListItem(BaseModel):
    scan_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    domains_count: Optional[int] = None
    accounts_count: Optional[int] = None
    folders_count: Optional[int] = None
    config_snapshot: Optional[dict] = None


class ScanDetail(ScanListItem):
    log_output: Optional[str] = None
    output_json_path: Optional[str] = None
    output_html_path: Optional[str] = None
    error_message: Optional[str] = None


# ── Background scan runner ─────────────────────────────────────────────────────

async def _run_scan(scan_id: str, config: InventoryConfig) -> None:
    """Запускает mailbox_inventory.py как subprocess и обновляет запись в БД."""
    output_json = str(SCAN_OUTPUT_DIR / f"{scan_id}.json")
    output_html = str(SCAN_OUTPUT_DIR / f"{scan_id}.html")

    cfg_dict = config.model_dump()
    cfg_dict["output"] = output_json
    cfg_dict["html_output"] = output_html
    cfg_dict["format"] = "json"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix=f"inv_{scan_id}_"
    ) as f:
        json.dump(cfg_dict, f)
        cfg_path = f.name

    script_path = Path(__file__).parent.parent / "services" / "mailbox_inventory.py"
    db = _get_session()

    try:
        scan = db.query(InventoryScan).filter_by(id=scan_id).first()
        if not scan:
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script_path), "--config", cfg_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            log_lines: list[str] = []
            assert proc.stdout
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                log_lines.append(line)
                # Сохраняем промежуточный лог каждые 50 строк
                if len(log_lines) % 50 == 0:
                    scan.log_output = "\n".join(log_lines[-500:])
                    db.commit()

            await proc.wait()
            returncode = proc.returncode

            scan.finished_at = datetime.utcnow()
            scan.log_output = "\n".join(log_lines[-500:])

            if returncode == 0 and Path(output_json).exists():
                scan.status = "success"
                scan.output_json_path = output_json
                scan.output_html_path = output_html if Path(output_html).exists() else None
                # Подсчёт статистики из JSON-результата
                try:
                    with open(output_json, encoding="utf-8") as jf:
                        result = json.load(jf)
                    scan.domains_count = len(result.get("domains", []))
                    scan.accounts_count = len(result.get("accounts", []))
                    scan.folders_count = len(result.get("folders", []))
                except Exception:
                    pass
            else:
                scan.status = "failed"
                scan.error_message = f"Exit code {returncode}"

        except Exception as exc:
            logger.exception("Scan %s failed with exception: %s", scan_id, exc)
            scan.status = "failed"
            scan.error_message = str(exc)
            scan.finished_at = datetime.utcnow()

        db.commit()
    finally:
        db.close()
        try:
            os.unlink(cfg_path)
        except Exception:
            pass


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/start", status_code=202)
async def start_scan(config: InventoryConfig, background_tasks: BackgroundTasks):
    """Запустить инвентаризацию IVA Mail."""
    scan_id = str(uuid.uuid4())
    db = _get_session()
    try:
        scan = InventoryScan(
            id=scan_id,
            status="running",
            config_snapshot=config.model_dump(),
        )
        db.add(scan)
        db.commit()
    finally:
        db.close()

    background_tasks.add_task(_run_scan, scan_id, config)
    return {"scan_id": scan_id, "status": "running"}


@router.get("/", response_model=List[ScanListItem])
def list_scans():
    """Список всех сканирований, новые сначала."""
    db = _get_session()
    try:
        scans = db.query(InventoryScan).order_by(InventoryScan.started_at.desc()).all()
        return [
            ScanListItem(
                scan_id=s.id,
                started_at=s.started_at,
                finished_at=s.finished_at,
                status=s.status,
                domains_count=s.domains_count,
                accounts_count=s.accounts_count,
                folders_count=s.folders_count,
                config_snapshot=s.config_snapshot,
            )
            for s in scans
        ]
    finally:
        db.close()


@router.get("/{scan_id}", response_model=ScanDetail)
def get_scan(scan_id: str):
    """Получить статус и лог конкретного сканирования."""
    db = _get_session()
    try:
        s = db.query(InventoryScan).filter_by(id=scan_id).first()
        if not s:
            raise HTTPException(status_code=404, detail="Scan not found")
        return ScanDetail(
            scan_id=s.id,
            started_at=s.started_at,
            finished_at=s.finished_at,
            status=s.status,
            domains_count=s.domains_count,
            accounts_count=s.accounts_count,
            folders_count=s.folders_count,
            config_snapshot=s.config_snapshot,
            log_output=s.log_output,
            output_json_path=s.output_json_path,
            output_html_path=s.output_html_path,
            error_message=s.error_message,
        )
    finally:
        db.close()


@router.get("/{scan_id}/download/json")
def download_json(scan_id: str):
    """Скачать JSON-результат сканирования."""
    db = _get_session()
    try:
        s = db.query(InventoryScan).filter_by(id=scan_id).first()
        if not s or not s.output_json_path:
            raise HTTPException(status_code=404, detail="JSON result not found")
        path = Path(s.output_json_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")
        return FileResponse(
            str(path),
            media_type="application/json",
            filename=f"inventory_{scan_id[:8]}.json",
        )
    finally:
        db.close()


@router.get("/{scan_id}/download/html")
def download_html(scan_id: str):
    """Открыть HTML-отчёт сканирования."""
    db = _get_session()
    try:
        s = db.query(InventoryScan).filter_by(id=scan_id).first()
        if not s or not s.output_html_path:
            raise HTTPException(status_code=404, detail="HTML result not found")
        path = Path(s.output_html_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")
        return HTMLResponse(content=path.read_text(encoding="utf-8"))
    finally:
        db.close()
