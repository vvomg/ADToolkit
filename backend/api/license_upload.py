"""
PHASE 3.5C: License Upload API

POST /api/deployment/{id}/upload-license
  — принимает .txt файл лицензии от администратора
  — уведомляет LicenseUploadWaiter → разблокирует оркестратор

GET /api/deployment/{id}/license-request
  — возвращает статус + инструкции

GET /api/deployment/{id}/license-request/download
  — скачать файл-запрос для отправки вендору
  (сгенерирован командой CMD:LicenseRequest)
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List

from ..infrastructure.license_manager import LicenseUploadWaiter
from ..core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

UPLOAD_DIR = Path(settings.upload_dir) / "licenses"
REQUEST_DIR = Path(settings.upload_dir) / "license_requests"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────

class LicenseUploadResponse(BaseModel):
    deployment_id: str
    filename: str
    size_bytes: int
    message: str


class LicenseRequestStatus(BaseModel):
    deployment_id: str
    status: str
    instructions: Optional[List[str]] = None
    request_file_available: bool = False
    message: str


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────

@router.post(
    "/{deployment_id}/upload-license",
    summary="Загрузить файл лицензии (.txt) от вендора",
    response_model=LicenseUploadResponse,
)
async def upload_license_file(
    deployment_id: str,
    license_file: UploadFile = File(
        ...,
        description="Файл лицензии IVA Mail (.txt), полученный от вендора"
    ),
) -> LicenseUploadResponse:
    """
    Принимает .txt файл лицензии.

    После загрузки оркестратор разблокируется и выполняет
    CMD:LicenseInstall на всех узлах кластера.
    """
    filename = license_file.filename or "license.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(
            status_code=400,
            detail=f"Ожидается файл .txt, получено: '{filename}'"
        )

    content = await license_file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл лицензии пустой")

    # Минимальная проверка формата
    text = content.decode("utf-8", errors="replace")
    if "-----BEGIN IVAMAIL LICENSE-----" not in text:
        raise HTTPException(
            status_code=400,
            detail="Файл не содержит блок '-----BEGIN IVAMAIL LICENSE-----'. "
                   "Убедитесь что передаёте корректный файл лицензии IVA Mail."
        )

    # Сохраняем файл на диск — оркестратор обнаружит его через файловый polling
    _ensure_dir(UPLOAD_DIR)
    file_path = UPLOAD_DIR / f"{deployment_id}_license.txt"
    file_path.write_bytes(content)

    size = len(content)
    logger.info(
        f"[LicenseUpload] deployment={deployment_id} "
        f"file={file_path.name} size={size}b"
    )

    # Опционально уведомляем in-memory waiter (если существует в этом воркере)
    waiter = LicenseUploadWaiter.get(deployment_id)
    if waiter:
        waiter.notify(str(file_path))
    else:
        logger.info(
            f"[LicenseUpload] Waiter для {deployment_id} не найден в этом воркере — "
            "оркестратор обнаружит файл через файловый polling."
        )

    return LicenseUploadResponse(
        deployment_id=deployment_id,
        filename=file_path.name,
        size_bytes=size,
        message=(
            f"Файл лицензии получен ({size} байт). "
            "Оркестратор выполняет CMD:LicenseInstall на всех узлах кластера."
        ),
    )


@router.get(
    "/{deployment_id}/license-request",
    summary="Статус ожидания лицензии и инструкции",
    response_model=LicenseRequestStatus,
)
async def get_license_request_status(deployment_id: str) -> LicenseRequestStatus:
    """
    Возвращает текущий статус фазы лицензирования.
    Если файл-запрос доступен — сообщает об этом (можно скачать).
    """
    waiter = LicenseUploadWaiter.get(deployment_id)

    request_file = REQUEST_DIR / f"{deployment_id}_request.txt"
    request_available = request_file.exists()

    if not waiter:
        return LicenseRequestStatus(
            deployment_id=deployment_id,
            status="not_waiting",
            request_file_available=request_available,
            message="Deployment не ожидает лицензии.",
        )

    if waiter._event.is_set():
        return LicenseRequestStatus(
            deployment_id=deployment_id,
            status="license_received",
            request_file_available=request_available,
            message="Файл лицензии получен, выполняется CMD:LicenseInstall.",
        )

    return LicenseRequestStatus(
        deployment_id=deployment_id,
        status="waiting_for_license",
        request_file_available=request_available,
        instructions=[
            "1. Скачайте файл-запрос:",
            f"   GET /api/deployment/{deployment_id}/license-request/download",
            "2. Отправьте файл-запрос вендору IVA Mail",
            "3. Получите от вендора файл лицензии (.txt)",
            "4. Загрузите файл лицензии:",
            f"   POST /api/deployment/{deployment_id}/upload-license",
        ],
        message="Ожидается загрузка файла лицензии (.txt) от администратора.",
    )


@router.get(
    "/{deployment_id}/license-request/download",
    summary="Скачать файл-запрос лицензии для отправки вендору",
)
async def download_license_request(deployment_id: str) -> FileResponse:
    """
    Отдаёт файл-запрос лицензии, сгенерированный через CMD:LicenseRequest.
    Этот файл нужно отправить вендору IVA Mail.
    """
    request_file = REQUEST_DIR / f"{deployment_id}_request.txt"

    if not request_file.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Файл-запрос для deployment '{deployment_id}' не найден. "
                "Убедитесь что фаза LICENSE_REQUEST уже выполнена."
            )
        )

    return FileResponse(
        path=str(request_file),
        media_type="text/plain",
        filename=f"license_request_{deployment_id}.txt",
    )
