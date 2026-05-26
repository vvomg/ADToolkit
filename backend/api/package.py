"""
Package Upload API

POST /api/package/upload          — загрузка .deb/.rpm файла с компьютера
POST /api/package/download-url    — скачать пакет по URL на машину toolkit
GET  /api/package/list            — список загруженных пакетов
DELETE /api/package/{filename}    — удаление пакета
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

PACKAGES_DIR = Path(settings.upload_dir) / "packages"
MAX_PACKAGE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
ALLOWED_EXTENSIONS = {'.deb', '.rpm'}
DOWNLOAD_TIMEOUT = 300  # 5 минут


def _get_packages_dir() -> Path:
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    return PACKAGES_DIR


def _safe_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r'[^\w\-.]', '_', name)
    return name or "package"


def _validate_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Неподдерживаемый формат: '{suffix}'. "
            f"Допустимы: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    return suffix


def _unique_path(directory: Path, filename: str) -> Path:
    path = directory / filename
    counter = 1
    while path.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        path = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return path


# ─── Upload from local file ───────────────────────────────────────

@router.post("/upload", summary="Загрузить пакет дистрибутива (.deb/.rpm)")
async def upload_package(file: UploadFile = File(...)):
    """
    Принимает .deb или .rpm файл, сохраняет в uploads/packages/.
    Возвращает путь для использования как local_path в PackageSource.
    """
    filename = file.filename or "package"
    _validate_extension(filename)

    packages_dir = _get_packages_dir()
    safe_name = _safe_filename(filename)
    file_path = _unique_path(packages_dir, safe_name)

    # Стриминговая запись (файлы могут быть большими)
    total = 0
    with open(file_path, 'wb') as f:
        while chunk := await file.read(1024 * 1024):  # 1 MB chunks
            total += len(chunk)
            if total > MAX_PACKAGE_SIZE:
                file_path.unlink(missing_ok=True)
                raise HTTPException(400, f"Файл превышает максимальный размер {MAX_PACKAGE_SIZE // (1024**2)} MB")
            f.write(chunk)

    logger.info(f"Пакет загружен: {file_path} ({total} байт)")

    return {
        "success": True,
        "filename": file_path.name,
        "path": str(file_path),
        "size_bytes": total,
        "format": Path(file_path).suffix.lstrip('.'),
    }


# ─── Download from URL ────────────────────────────────────────────

class DownloadURLRequest(BaseModel):
    url: str
    filename: Optional[str] = None


@router.post("/download-url", summary="Скачать пакет по URL")
async def download_package_from_url(req: DownloadURLRequest):
    """
    Скачивает .deb/.rpm пакет по HTTP/HTTPS/FTP URL на машину toolkit.
    После этого файл доступен как local_path в PackageSource.
    """
    url = req.url.strip()
    if not url.startswith(('http://', 'https://', 'ftp://')):
        raise HTTPException(400, "URL должен начинаться с http://, https:// или ftp://")

    # Определяем имя файла
    filename = req.filename or url.split('/')[-1].split('?')[0]
    if not filename:
        filename = "ivamail_package"

    # Добавляем расширение если нет
    if not Path(filename).suffix:
        filename += ".deb"

    _validate_extension(filename)

    packages_dir = _get_packages_dir()
    safe_name = _safe_filename(filename)
    file_path = _unique_path(packages_dir, safe_name)

    logger.info(f"Скачивание пакета: {url} → {file_path}")

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            total = 0
            with open(file_path, 'wb') as f:
                async with client.stream('GET', url) as response:
                    if response.status_code != 200:
                        raise HTTPException(
                            400,
                            f"Сервер вернул {response.status_code} для URL: {url}"
                        )
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_PACKAGE_SIZE:
                            file_path.unlink(missing_ok=True)
                            raise HTTPException(400, "Файл превышает максимальный размер 2 GB")
                        f.write(chunk)

    except httpx.RequestError as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Ошибка скачивания: {e}")

    logger.info(f"Пакет скачан: {file_path} ({total} байт)")

    return {
        "success": True,
        "filename": file_path.name,
        "path": str(file_path),
        "size_bytes": total,
        "format": Path(file_path).suffix.lstrip('.'),
        "source_url": url,
    }


# ─── List packages ────────────────────────────────────────────────

@router.get("/list", summary="Список загруженных пакетов")
async def list_packages():
    packages_dir = _get_packages_dir()
    packages = []
    for f in sorted(packages_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
            packages.append({
                "filename": f.name,
                "path": str(f),
                "size_bytes": f.stat().st_size,
                "format": f.suffix.lstrip('.'),
            })
    return {"packages": packages}


# ─── Delete package ───────────────────────────────────────────────

@router.delete("/{filename}", summary="Удалить пакет")
async def delete_package(filename: str):
    safe_name = _safe_filename(filename)
    packages_dir = _get_packages_dir()
    file_path = packages_dir / safe_name

    if not file_path.exists():
        raise HTTPException(404, f"Пакет '{safe_name}' не найден")

    file_path.unlink()
    logger.info(f"Пакет удалён: {file_path}")
    return {"success": True, "deleted": safe_name}
