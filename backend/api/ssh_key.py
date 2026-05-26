"""
SSH Key Upload API

POST /api/ssh-key/upload  — загрузка приватного SSH ключа
GET  /api/ssh-key/list    — список загруженных ключей
DELETE /api/ssh-key/{name} — удаление ключа
"""

import os
import re
import stat
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from ..core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

SSH_KEYS_DIR = Path(settings.upload_dir) / "ssh_keys"
MAX_KEY_SIZE = 64 * 1024  # 64 KB — приватный ключ никогда не бывает больше


def _get_keys_dir() -> Path:
    SSH_KEYS_DIR.mkdir(parents=True, exist_ok=True)
    return SSH_KEYS_DIR


def _validate_key_content(content: bytes) -> str:
    """Проверяет что файл похож на SSH приватный ключ."""
    try:
        text = content.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise HTTPException(400, "Файл должен быть текстовым (UTF-8)")

    valid_headers = [
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
    ]
    if not any(text.startswith(h) for h in valid_headers):
        raise HTTPException(
            400,
            "Файл не является SSH приватным ключом. "
            "Ожидается файл начинающийся с '-----BEGIN ... PRIVATE KEY-----'"
        )
    return text


def _safe_filename(name: str) -> str:
    """Очищает имя файла от опасных символов."""
    name = Path(name).name  # убираем path traversal
    name = re.sub(r'[^\w\-.]', '_', name)
    if not name:
        name = "ssh_key"
    return name


@router.post("/upload", summary="Загрузить SSH приватный ключ")
async def upload_ssh_key(file: UploadFile = File(...)):
    """
    Принимает файл приватного SSH ключа (.pem, .key, id_rsa и т.п.).
    Сохраняет в uploads/ssh_keys/ с правами 600.
    Возвращает путь для использования в ssh_key_path.
    """
    # Проверка размера
    content = await file.read()
    if len(content) > MAX_KEY_SIZE:
        raise HTTPException(400, f"Файл слишком большой (макс {MAX_KEY_SIZE // 1024} KB)")

    # Валидация содержимого
    _validate_key_content(content)

    # Сохранение
    keys_dir = _get_keys_dir()
    safe_name = _safe_filename(file.filename or "ssh_key")
    file_path = keys_dir / safe_name

    # Если файл с таким именем уже есть — добавляем суффикс
    counter = 1
    while file_path.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix or ""
        file_path = keys_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    file_path.write_bytes(content)

    # Права 600 (только владелец читает/пишет) — критично для SSH
    try:
        os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception as e:
        logger.warning(f"Не удалось установить права 600 на {file_path}: {e}")

    logger.info(f"SSH ключ загружен: {file_path}")

    return {
        "success": True,
        "filename": file_path.name,
        "path": str(file_path),
        "size_bytes": len(content),
    }


@router.get("/list", summary="Список загруженных SSH ключей")
async def list_ssh_keys():
    """Возвращает список загруженных SSH ключей."""
    keys_dir = _get_keys_dir()
    keys = []
    for f in sorted(keys_dir.iterdir()):
        if f.is_file():
            keys.append({
                "filename": f.name,
                "path": str(f),
                "size_bytes": f.stat().st_size,
            })
    return {"keys": keys}


@router.delete("/{filename}", summary="Удалить SSH ключ")
async def delete_ssh_key(filename: str):
    """Удаляет загруженный SSH ключ по имени файла."""
    safe_name = _safe_filename(filename)
    keys_dir = _get_keys_dir()
    file_path = keys_dir / safe_name

    if not file_path.exists():
        raise HTTPException(404, f"Ключ '{safe_name}' не найден")

    file_path.unlink()
    logger.info(f"SSH ключ удалён: {file_path}")
    return {"success": True, "deleted": safe_name}
