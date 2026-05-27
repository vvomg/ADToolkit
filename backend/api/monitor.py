"""
Monitor Nodes API

CRUD-реестр нод для дашборда мониторинга + импорт из деплоя + разовый probe.

GET    /api/monitor/nodes                — список нод (без паролей)
POST   /api/monitor/nodes                — добавить ноду
PUT    /api/monitor/nodes/{id}           — обновить ноду
DELETE /api/monitor/nodes/{id}           — удалить ноду
POST   /api/monitor/nodes/import-deploy  — импорт из последнего деплоя
POST   /api/monitor/nodes/probe          — разовая проверка ноды без сохранения
"""

import asyncio
import json
import logging
import re
import socket
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import paramiko
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..cmd.cmd_registry import EnrichedCommand, enrich_help_output, full_reference, lookup
from ..core.config import settings
from ..infrastructure.cmd_client import CMDClient, CMDError, CMDConnectionError, CMDAuthError
from ..models.db_models import DeploymentRun, MonitorCluster, MonitorNode, get_db_engine, init_db

logger = logging.getLogger(__name__)

router = APIRouter()

_CMD_TIMEOUT = 8.0
_SSH_CONNECT_TIMEOUT = 8
_SSH_CMD_TIMEOUT = 5
_SSH_FULL_TIMEOUT = 10


# ─── DB session helper ───────────────────────────────────────────

def _get_session() -> Session:
    engine = get_db_engine(settings.get_database_sync_url())
    init_db(engine)
    return Session(engine)


# ─── Pydantic models ─────────────────────────────────────────────

class ClusterPublic(BaseModel):
    id: int
    name: str
    color: str
    description: Optional[str] = None
    sort_order: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ClusterCreate(BaseModel):
    name: str
    color: str = "blue"
    description: Optional[str] = None
    sort_order: int = 0


class ClusterUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None


class MonitorNodePublic(BaseModel):
    """Публичный вид ноды — пароли скрыты, только флаг наличия."""
    id: int
    ip: str
    hostname: Optional[str] = None
    display_name: Optional[str] = None
    node_type: str
    cluster_id: Optional[int] = None
    ssh_user: str
    ssh_auth_mode: str
    ssh_key_path: Optional[str] = None
    ssh_port: int
    has_ssh_password: bool
    cmd_user: str
    has_cmd_password: bool
    sort_order: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MonitorNodeCreate(BaseModel):
    ip: str
    hostname: Optional[str] = None
    display_name: Optional[str] = None
    node_type: str
    cluster_id: Optional[int] = None
    ssh_user: str = "user"
    ssh_auth_mode: str = "password"   # "password" | "key"
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    ssh_port: int = 22
    cmd_user: str = "admin"
    cmd_password: Optional[str] = "admin"
    sort_order: int = 0


class MonitorNodeUpdate(BaseModel):
    ip: Optional[str] = None
    hostname: Optional[str] = None
    display_name: Optional[str] = None
    node_type: Optional[str] = None
    cluster_id: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_auth_mode: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    ssh_port: Optional[int] = None
    cmd_user: Optional[str] = None
    cmd_password: Optional[str] = None
    sort_order: Optional[int] = None


class ImportDeployResponse(BaseModel):
    imported: int
    skipped: int
    nodes: List[MonitorNodePublic]


class ProbeRequest(BaseModel):
    ip: str
    node_type: str
    ssh_user: str = "user"
    ssh_auth_mode: str = "password"
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    ssh_port: int = 22
    cmd_user: str = "admin"
    cmd_password: Optional[str] = "admin"


class ProbeResult(BaseModel):
    ip: str
    node_type: str
    online: bool
    error: Optional[str] = None
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    os_pretty: Optional[str] = None
    # CMD fields (ivamail_* only)
    version: Optional[str] = None
    uptime: Optional[str] = None
    load: Optional[float] = None
    connections: Optional[int] = None
    checked_at: datetime


# ─── Helpers: ORM → public ───────────────────────────────────────

def _to_public(node: MonitorNode) -> MonitorNodePublic:
    return MonitorNodePublic(
        id=node.id,
        ip=node.ip,
        hostname=node.hostname,
        display_name=node.display_name,
        node_type=node.node_type,
        cluster_id=node.cluster_id,
        ssh_user=node.ssh_user,
        ssh_auth_mode=node.ssh_auth_mode,
        ssh_key_path=node.ssh_key_path,
        ssh_port=node.ssh_port,
        has_ssh_password=bool(node.ssh_password),
        cmd_user=node.cmd_user,
        has_cmd_password=bool(node.cmd_password),
        sort_order=node.sort_order,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


# ─── SSH OS probe (paramiko, sync → executor) ────────────────────

def _parse_os_release(output: str) -> Dict[str, Any]:
    """Парсит вывод cat /etc/os-release в словарь os_name/os_version/os_pretty."""
    result: Dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        result[key.upper()] = value

    return {
        "os_name": result.get("NAME"),
        "os_version": result.get("VERSION_ID"),
        "os_pretty": result.get("PRETTY_NAME"),
        "online": True,
        "error": None,
    }


async def _probe_ssh_os(
    ip: str,
    ssh_user: str,
    ssh_password: Optional[str],
    ssh_key_path: Optional[str],
    ssh_port: int = 22,
) -> Dict[str, Any]:
    """SSH-проверка: cat /etc/os-release. Возвращает os_name/os_version/os_pretty/online/error."""

    def _do() -> str:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: Dict[str, Any] = dict(
            hostname=ip,
            port=ssh_port,
            username=ssh_user,
            timeout=_SSH_CONNECT_TIMEOUT,
            allow_agent=False,
            look_for_keys=False,
        )
        if ssh_key_path:
            connect_kwargs["key_filename"] = ssh_key_path
        else:
            connect_kwargs["password"] = ssh_password or ""
        client.connect(**connect_kwargs)
        _, stdout, _ = client.exec_command("cat /etc/os-release 2>/dev/null", timeout=_SSH_CMD_TIMEOUT)
        output = stdout.read().decode("utf-8", errors="replace")
        client.close()
        return output

    try:
        loop = asyncio.get_running_loop()
        output = await asyncio.wait_for(loop.run_in_executor(None, _do), timeout=_SSH_FULL_TIMEOUT)
        return _parse_os_release(output)
    except paramiko.AuthenticationException as e:
        return {"os_name": None, "os_version": None, "os_pretty": None, "online": False, "error": f"Ошибка аутентификации SSH: {e}"}
    except paramiko.SSHException as e:
        return {"os_name": None, "os_version": None, "os_pretty": None, "online": False, "error": f"SSH ошибка: {e}"}
    except (socket.timeout, asyncio.TimeoutError, TimeoutError):
        return {"os_name": None, "os_version": None, "os_pretty": None, "online": False, "error": f"Таймаут SSH ({_SSH_FULL_TIMEOUT}s)"}
    except Exception as e:
        return {"os_name": None, "os_version": None, "os_pretty": None, "online": False, "error": str(e)}


# ─── CMD probe (reused from cluster.py pattern) ──────────────────

def _parse_system_info(text: str) -> Dict[str, Any]:
    """Парсит текст ответа SystemInfo в словарь.

    Стратегии (в порядке приоритета):
    1. Весь текст — JSON-объект или массив объектов
    2. Отдельные строки — JSON-объект (тело пришло после «200 OK»)
    3. Построчный парсинг «key: value»
    """
    text = text.strip()
    if not text:
        return {}

    # 1. Весь текст как JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed:
            merged: Dict[str, Any] = {}
            for item in parsed:
                if isinstance(item, dict):
                    merged.update(item)
            return merged
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Каждая строка как отдельный JSON-объект
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

    # 3. Парсинг «key: value»
    result: Dict[str, Any] = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
        elif line.strip():
            result[f"line_{len(result)}"] = line.strip()
    return result


def _extract_cmd_fields(info: Dict[str, Any]) -> Dict[str, Any]:
    lower = {k.lower(): v for k, v in info.items()}

    version: Optional[str] = None
    for key in ("server version", "version", "serverversion", "buildversion", "ver"):
        if key in lower:
            version = str(lower[key])
            break

    uptime: Optional[str] = None
    for key in ("system uptime", "uptime", "starttime", "started", "uptimeseconds"):
        if key in lower:
            uptime = str(lower[key])
            break

    load: Optional[float] = None
    for key in ("loadavg", "load", "cpuload", "cpu"):
        if key in lower:
            try:
                load = float(lower[key])
            except (TypeError, ValueError):
                pass
            break

    connections: Optional[int] = None
    for key in ("connections", "activeconnections", "conncount", "total accounts"):
        if key in lower:
            try:
                connections = int(lower[key])
            except (TypeError, ValueError):
                pass
            break

    cluster_status: Optional[str] = None
    for key in ("cluster status", "clusterstatus", "cluster_status"):
        if key in lower:
            cluster_status = str(lower[key])
            break

    return {"version": version, "uptime": uptime, "load": load, "connections": connections, "cluster_status": cluster_status}


async def _probe_cmd(ip: str, cmd_user: str, cmd_password: str) -> Dict[str, Any]:
    """CMD SystemInfo проверка. Возвращает version/uptime/load/connections/online/error."""
    try:
        async with CMDClient(ip, settings.ivamail_port, timeout=_CMD_TIMEOUT) as cmd:
            await cmd.authenticate(cmd_user, cmd_password)
            resp = await cmd.system_info()
            if not resp.ok:
                return {"online": False, "error": f"SystemInfo код {resp.code}: {resp.text[:200]}", "version": None, "uptime": None, "load": None, "connections": None}
            raw = _parse_system_info(resp.text)
            fields = _extract_cmd_fields(raw)
            return {"online": True, "error": None, **fields}
    except (CMDConnectionError, CMDAuthError, CMDError) as e:
        return {"online": False, "error": str(e), "version": None, "uptime": None, "load": None, "connections": None}
    except asyncio.TimeoutError:
        return {"online": False, "error": f"Таймаут CMD ({_CMD_TIMEOUT}s)", "version": None, "uptime": None, "load": None, "connections": None}
    except Exception as e:
        return {"online": False, "error": str(e), "version": None, "uptime": None, "load": None, "connections": None}


# ─── CRUD endpoints ──────────────────────────────────────────────

@router.get(
    "/nodes",
    summary="Список нод мониторинга",
    response_model=List[MonitorNodePublic],
)
def list_nodes() -> List[MonitorNodePublic]:
    session = _get_session()
    try:
        nodes = session.query(MonitorNode).order_by(MonitorNode.sort_order, MonitorNode.id).all()
        return [_to_public(n) for n in nodes]
    finally:
        session.close()


@router.post(
    "/nodes",
    summary="Добавить ноду мониторинга",
    response_model=MonitorNodePublic,
    status_code=201,
)
def create_node(body: MonitorNodeCreate) -> MonitorNodePublic:
    session = _get_session()
    try:
        existing = session.query(MonitorNode).filter(MonitorNode.ip == body.ip).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Нода с IP {body.ip} уже существует")
        node = MonitorNode(
            ip=body.ip,
            hostname=body.hostname,
            display_name=body.display_name,
            node_type=body.node_type,
            cluster_id=body.cluster_id,
            ssh_user=body.ssh_user,
            ssh_auth_mode=body.ssh_auth_mode,
            ssh_password=body.ssh_password,
            ssh_key_path=body.ssh_key_path,
            ssh_port=body.ssh_port,
            cmd_user=body.cmd_user,
            cmd_password=body.cmd_password,
            sort_order=body.sort_order,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        return _to_public(node)
    finally:
        session.close()


@router.put(
    "/nodes/{node_id}",
    summary="Обновить ноду мониторинга",
    response_model=MonitorNodePublic,
)
def update_node(node_id: int, body: MonitorNodeUpdate) -> MonitorNodePublic:
    session = _get_session()
    try:
        node = session.query(MonitorNode).filter(MonitorNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail=f"Нода {node_id} не найдена")

        # Проверка уникальности IP при смене
        if body.ip is not None and body.ip != node.ip:
            conflict = session.query(MonitorNode).filter(MonitorNode.ip == body.ip).first()
            if conflict:
                raise HTTPException(status_code=409, detail=f"Нода с IP {body.ip} уже существует")

        update_data = body.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(node, field, value)
        node.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(node)
        return _to_public(node)
    finally:
        session.close()


@router.delete(
    "/nodes/{node_id}",
    summary="Удалить ноду мониторинга",
    status_code=204,
)
def delete_node(node_id: int) -> None:
    session = _get_session()
    try:
        node = session.query(MonitorNode).filter(MonitorNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail=f"Нода {node_id} не найдена")
        session.delete(node)
        session.commit()
    finally:
        session.close()


# ─── Import from last deploy ─────────────────────────────────────

@router.post(
    "/nodes/import-deploy",
    summary="Импортировать ноды из последнего деплоя",
    response_model=ImportDeployResponse,
)
def import_from_deploy(
    force: bool = Query(default=False, description="Импортировать даже если таблица не пуста"),
) -> ImportDeployResponse:
    session = _get_session()
    try:
        existing_count = session.query(MonitorNode).count()
        if existing_count > 0 and not force:
            nodes = session.query(MonitorNode).order_by(MonitorNode.sort_order, MonitorNode.id).all()
            return ImportDeployResponse(imported=0, skipped=existing_count, nodes=[_to_public(n) for n in nodes])

        # Собираем ноды из последних 10 деплоев
        runs = (
            session.query(DeploymentRun)
            .filter(DeploymentRun.cluster_config.isnot(None))
            .order_by(DeploymentRun.created_at.desc())
            .limit(10)
            .all()
        )

        # node_type mapping
        _TYPE_MAP = {
            "backends":           "ivamail_backend",
            "frontends":          "ivamail_frontend",
            "database_server":    "nfs",
            "haproxy_servers":    "haproxy",
            "monitoring_servers": "monitoring",
        }

        candidates: Dict[str, Dict[str, Any]] = {}  # ip → node kwargs

        for run in runs:
            cc = run.cluster_config or {}
            for field, node_type in _TYPE_MAP.items():
                servers = cc.get(field)
                if not servers:
                    continue
                items = servers if isinstance(servers, list) else [servers]
                for s in items:
                    ip = s.get("ip", "").strip()
                    if not ip or ip in candidates:
                        continue
                    hostname = s.get("hostname") or f"{node_type[:2]}-{ip.replace('.', '-')}"
                    candidates[ip] = {
                        "ip": ip,
                        "hostname": hostname,
                        "node_type": node_type,
                    }

        imported = 0
        skipped = 0
        created_nodes: List[MonitorNode] = []

        for ip, meta in candidates.items():
            existing = session.query(MonitorNode).filter(MonitorNode.ip == ip).first()
            if existing:
                skipped += 1
                created_nodes.append(existing)
                continue
            node = MonitorNode(
                ip=meta["ip"],
                hostname=meta["hostname"],
                node_type=meta["node_type"],
                ssh_user="user",
                ssh_auth_mode="password",
                ssh_password="DefaultP4ss",
                ssh_port=22,
                cmd_user="admin",
                cmd_password="admin",
                sort_order=0,
            )
            session.add(node)
            session.flush()
            imported += 1
            created_nodes.append(node)

        session.commit()
        for n in created_nodes:
            session.refresh(n)

        return ImportDeployResponse(
            imported=imported,
            skipped=skipped,
            nodes=[_to_public(n) for n in created_nodes],
        )
    finally:
        session.close()


# ─── Probe endpoint (без сохранения) ────────────────────────────

@router.post(
    "/nodes/probe",
    summary="Разовая проверка ноды без сохранения в БД",
    response_model=ProbeResult,
)
async def probe_node(body: ProbeRequest) -> ProbeResult:
    now = datetime.utcnow()
    is_ivamail = body.node_type in ("ivamail_backend", "ivamail_frontend")

    if is_ivamail:
        ssh_task = _probe_ssh_os(body.ip, body.ssh_user, body.ssh_password, body.ssh_key_path, body.ssh_port)
        cmd_task = _probe_cmd(body.ip, body.cmd_user or "admin", body.cmd_password or "admin")
        ssh_result, cmd_result = await asyncio.gather(ssh_task, cmd_task)

        # CMD определяет online; SSH даёт OS-поля бонусом
        online = cmd_result.get("online", False)
        error = cmd_result.get("error") if not online else (ssh_result.get("error") if not ssh_result.get("online") else None)

        return ProbeResult(
            ip=body.ip,
            node_type=body.node_type,
            online=online,
            error=error,
            os_name=ssh_result.get("os_name"),
            os_version=ssh_result.get("os_version"),
            os_pretty=ssh_result.get("os_pretty"),
            version=cmd_result.get("version"),
            uptime=cmd_result.get("uptime"),
            load=cmd_result.get("load"),
            connections=cmd_result.get("connections"),
            checked_at=now,
        )
    else:
        ssh_result = await _probe_ssh_os(body.ip, body.ssh_user, body.ssh_password, body.ssh_key_path, body.ssh_port)
        return ProbeResult(
            ip=body.ip,
            node_type=body.node_type,
            online=ssh_result.get("online", False),
            error=ssh_result.get("error"),
            os_name=ssh_result.get("os_name"),
            os_version=ssh_result.get("os_version"),
            os_pretty=ssh_result.get("os_pretty"),
            checked_at=now,
        )


# ─── CMD Help / Command discovery helpers ────────────────────────

def _parse_help_lines(resp_text: str) -> List[str]:
    """Извлекает имена команд из ответа HELP.

    Сервер возвращает JSON-массив строк вида:
      ["CommandName args", "OtherCommand: param", ...]
    Из каждой строки берём первое слово до пробела, «:» или «(».
    """
    names: List[str] = []

    # Попытка спарсить как JSON-массив
    try:
        items = json.loads(resp_text)
        if isinstance(items, list):
            for item in items:
                raw = str(item).strip()
                # Берём часть до первого «:», «(» или пробела
                name = re.split(r'[ :(]', raw)[0].strip()
                if name and re.match(r'^[A-Za-z]', name):
                    names.append(name)
            return names
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: построчный парсинг (старый формат или NNN-multiline)
    for line in resp_text.splitlines():
        line = line.strip().strip('"').strip("'").strip(",")
        if not line:
            continue
        name = re.split(r'[ :(]', line)[0].strip()
        if name and re.match(r'^[A-Za-z]', name):
            names.append(name)

    return names


# ─── CMD discovery endpoint ──────────────────────────────────────

@router.post(
    "/nodes/{node_id}/discover-commands",
    summary="Опросить ноду HELP и сохранить список команд",
)
async def discover_commands(node_id: int) -> Dict[str, Any]:
    """
    Подключается к ноде по CMD, вызывает HELP, обогащает данными из CMD-methods.md,
    сохраняет в БД и возвращает результат.
    """
    session = _get_session()
    try:
        node = session.query(MonitorNode).filter(MonitorNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail=f"Нода {node_id} не найдена")

        if node.node_type not in ("ivamail_backend", "ivamail_frontend"):
            raise HTTPException(
                status_code=400,
                detail=f"discover-commands доступен только для ivamail_* нод, текущий тип: {node.node_type}",
            )

        cmd_user = node.cmd_user or "admin"
        cmd_password = node.cmd_password or "admin"

        try:
            async with CMDClient(node.ip, settings.ivamail_port, timeout=_CMD_TIMEOUT) as cmd:
                await cmd.authenticate(cmd_user, cmd_password)
                resp = await cmd.help()
        except (CMDConnectionError, CMDAuthError, CMDError) as e:
            raise HTTPException(status_code=502, detail=f"CMD ошибка: {e}")
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=f"Таймаут CMD ({_CMD_TIMEOUT}s)")

        command_names = _parse_help_lines(resp.text)
        enriched = enrich_help_output(command_names)

        fetched_at = datetime.utcnow()
        node.cmd_commands = json.dumps([asdict(c) for c in enriched], ensure_ascii=False)
        node.cmd_help_fetched_at = fetched_at
        session.commit()

        documented_count = sum(1 for c in enriched if c.documented)

        return {
            "commands": [asdict(c) for c in enriched],
            "count": len(enriched),
            "documented": documented_count,
            "fetched_at": fetched_at.isoformat(),
        }
    finally:
        session.close()


@router.get(
    "/nodes/{node_id}/commands",
    summary="Получить сохранённый список команд ноды",
)
def get_node_commands(node_id: int) -> Any:
    """Возвращает ранее сохранённый список команд из БД."""
    session = _get_session()
    try:
        node = session.query(MonitorNode).filter(MonitorNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail=f"Нода {node_id} не найдена")

        if not node.cmd_commands:
            from fastapi.responses import Response
            return Response(status_code=204)

        commands = json.loads(node.cmd_commands)
        return {
            "commands": commands,
            "count": len(commands),
            "fetched_at": node.cmd_help_fetched_at.isoformat() if node.cmd_help_fetched_at else None,
        }
    finally:
        session.close()


# ─── CMD reference endpoints (статика из MD) ─────────────────────

@router.get(
    "/nodes/{node_id}/credentials",
    summary="Получить CMD-реквизиты ноды",
)
def get_node_credentials(node_id: int) -> dict:
    """Возвращает cmd_user и cmd_password для подстановки в UI."""
    session = _get_session()
    try:
        node = session.query(MonitorNode).filter(MonitorNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail=f"Нода {node_id} не найдена")
        return {
            "cmd_user": node.cmd_user or "admin",
            "cmd_password": node.cmd_password or "",
        }
    finally:
        session.close()


@router.get(
    "/cmd-reference",
    summary="Полный справочник CMD-команд из документации",
)
def get_cmd_reference() -> List[dict]:
    """Возвращает все команды из CMD-methods.md как список."""
    return full_reference()


@router.get(
    "/cmd-reference/{command_name}",
    summary="Описание одной CMD-команды из документации",
)
def get_cmd_reference_command(command_name: str) -> dict:
    """Возвращает описание конкретной команды из CMD-methods.md."""
    doc = lookup(command_name)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Команда '{command_name}' не найдена в документации")
    return {
        "name": doc.name,
        "syntax": doc.syntax,
        "section": doc.section,
        "description": doc.description,
        "documented": True,
        "available": None,
    }


# ─── Кластеры мониторинга ────────────────────────────────────────

@router.get(
    "/clusters",
    summary="Список кластеров мониторинга",
    response_model=List[ClusterPublic],
)
def list_clusters() -> List[ClusterPublic]:
    session = _get_session()
    try:
        clusters = (
            session.query(MonitorCluster)
            .order_by(MonitorCluster.sort_order, MonitorCluster.id)
            .all()
        )
        return [
            ClusterPublic(
                id=c.id,
                name=c.name,
                color=c.color,
                description=c.description,
                sort_order=c.sort_order,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in clusters
        ]
    finally:
        session.close()


@router.post(
    "/clusters",
    summary="Создать кластер мониторинга",
    response_model=ClusterPublic,
    status_code=201,
)
def create_cluster(body: ClusterCreate) -> ClusterPublic:
    session = _get_session()
    try:
        cluster = MonitorCluster(
            name=body.name,
            color=body.color,
            description=body.description,
            sort_order=body.sort_order,
        )
        session.add(cluster)
        session.commit()
        session.refresh(cluster)
        return ClusterPublic(
            id=cluster.id,
            name=cluster.name,
            color=cluster.color,
            description=cluster.description,
            sort_order=cluster.sort_order,
            created_at=cluster.created_at,
            updated_at=cluster.updated_at,
        )
    finally:
        session.close()


@router.put(
    "/clusters/{cluster_id}",
    summary="Обновить кластер мониторинга",
    response_model=ClusterPublic,
)
def update_cluster(cluster_id: int, body: ClusterUpdate) -> ClusterPublic:
    session = _get_session()
    try:
        cluster = session.query(MonitorCluster).filter(MonitorCluster.id == cluster_id).first()
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Кластер {cluster_id} не найден")
        update_data = body.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(cluster, field, value)
        cluster.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(cluster)
        return ClusterPublic(
            id=cluster.id,
            name=cluster.name,
            color=cluster.color,
            description=cluster.description,
            sort_order=cluster.sort_order,
            created_at=cluster.created_at,
            updated_at=cluster.updated_at,
        )
    finally:
        session.close()


@router.delete(
    "/clusters/{cluster_id}",
    summary="Удалить кластер (ноды остаются, cluster_id → null)",
    status_code=204,
)
def delete_cluster(cluster_id: int) -> None:
    session = _get_session()
    try:
        cluster = session.query(MonitorCluster).filter(MonitorCluster.id == cluster_id).first()
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Кластер {cluster_id} не найден")
        # Отвязываем ноды перед удалением
        session.query(MonitorNode).filter(MonitorNode.cluster_id == cluster_id).update(
            {"cluster_id": None}, synchronize_session=False
        )
        session.delete(cluster)
        session.commit()
    finally:
        session.close()


@router.patch(
    "/nodes/{node_id}/cluster",
    summary="Переместить ноду в кластер (или убрать из кластера)",
    response_model=MonitorNodePublic,
)
def assign_node_cluster(
    node_id: int,
    cluster_id: Optional[int] = None,
) -> MonitorNodePublic:
    """cluster_id=null (или не передан) → убирает из кластера."""
    session = _get_session()
    try:
        node = session.query(MonitorNode).filter(MonitorNode.id == node_id).first()
        if not node:
            raise HTTPException(status_code=404, detail=f"Нода {node_id} не найдена")
        if cluster_id is not None:
            exists = session.query(MonitorCluster).filter(MonitorCluster.id == cluster_id).first()
            if not exists:
                raise HTTPException(status_code=404, detail=f"Кластер {cluster_id} не найден")
        node.cluster_id = cluster_id
        node.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(node)
        return _to_public(node)
    finally:
        session.close()
