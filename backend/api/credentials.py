"""
CRUD для credential profiles (SSH / CMD / PostgreSQL).

POST   /api/credentials/           — создать профиль
GET    /api/credentials/           — список профилей (без паролей)
GET    /api/credentials/{name}     — профиль по имени (с паролями)
PUT    /api/credentials/{name}     — обновить профиль
DELETE /api/credentials/{name}     — удалить профиль

Хранилище: SQLite (credentials.db в корне проекта).
Пароли хранятся открытым текстом — для dev-среды.
В продакшн заменить на шифрование (Fernet / age).
"""
import sqlite3
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# credentials.db рядом с корнем проекта (родитель backend/)
DB_PATH = Path(__file__).parent.parent.parent / "credentials.db"


# ─── Pydantic models ──────────────────────────────────────────────

class CredentialProfile(BaseModel):
    name: str
    ssh_key_path: Optional[str] = None
    ssh_user: str = "root"
    cmd_user: Optional[str] = None
    cmd_password: Optional[str] = None
    pg_user: Optional[str] = "postgres"
    pg_password: Optional[str] = None


class CredentialProfilePublic(BaseModel):
    """Публичное представление — без паролей."""
    name: str
    ssh_key_path: Optional[str] = None
    ssh_user: str
    cmd_user: Optional[str] = None
    pg_user: Optional[str] = None


# ─── DB helpers ───────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            name         TEXT PRIMARY KEY,
            ssh_key_path TEXT,
            ssh_user     TEXT NOT NULL DEFAULT 'root',
            cmd_user     TEXT,
            cmd_password TEXT,
            pg_user      TEXT DEFAULT 'postgres',
            pg_password  TEXT
        )
    """)
    conn.commit()
    return conn


def _row_to_profile(row: sqlite3.Row) -> CredentialProfile:
    return CredentialProfile(**dict(row))


def _row_to_public(row: sqlite3.Row) -> CredentialProfilePublic:
    d = dict(row)
    return CredentialProfilePublic(
        name=d["name"],
        ssh_key_path=d.get("ssh_key_path"),
        ssh_user=d.get("ssh_user", "root"),
        cmd_user=d.get("cmd_user"),
        pg_user=d.get("pg_user"),
    )


# ─── Endpoints ────────────────────────────────────────────────────

@router.post("/", response_model=CredentialProfilePublic, status_code=201)
async def create_credential(profile: CredentialProfile) -> CredentialProfilePublic:
    """Создать новый credential profile."""
    with _conn() as conn:
        try:
            conn.execute(
                """INSERT INTO credentials
                   (name, ssh_key_path, ssh_user, cmd_user, cmd_password, pg_user, pg_password)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (profile.name, profile.ssh_key_path, profile.ssh_user,
                 profile.cmd_user, profile.cmd_password,
                 profile.pg_user, profile.pg_password),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(400, f"Профиль '{profile.name}' уже существует")

    logger.info("Created credential profile: %s", profile.name)
    return CredentialProfilePublic(**profile.model_dump())


@router.get("/", response_model=list[CredentialProfilePublic])
async def list_credentials() -> list[CredentialProfilePublic]:
    """Список всех профилей без паролей."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT name, ssh_key_path, ssh_user, cmd_user, pg_user FROM credentials ORDER BY name"
        ).fetchall()
    return [_row_to_public(r) for r in rows]


@router.get("/{name}", response_model=CredentialProfile)
async def get_credential(name: str) -> CredentialProfile:
    """Получить профиль с паролями (для передачи оркестратору)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM credentials WHERE name = ?", (name,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Профиль '{name}' не найден")
    return _row_to_profile(row)


@router.put("/{name}", response_model=CredentialProfilePublic)
async def update_credential(name: str, profile: CredentialProfile) -> CredentialProfilePublic:
    """Обновить существующий профиль."""
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE credentials
               SET ssh_key_path=?, ssh_user=?, cmd_user=?, cmd_password=?, pg_user=?, pg_password=?
               WHERE name=?""",
            (profile.ssh_key_path, profile.ssh_user,
             profile.cmd_user, profile.cmd_password,
             profile.pg_user, profile.pg_password, name),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, f"Профиль '{name}' не найден")

    logger.info("Updated credential profile: %s", name)
    return CredentialProfilePublic(**profile.model_dump())


@router.delete("/{name}", status_code=204)
async def delete_credential(name: str) -> None:
    """Удалить профиль."""
    with _conn() as conn:
        cur = conn.execute("DELETE FROM credentials WHERE name = ?", (name,))
        if cur.rowcount == 0:
            raise HTTPException(404, f"Профиль '{name}' не найден")

    logger.info("Deleted credential profile: %s", name)
