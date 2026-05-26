"""
Cluster Live Status API

GET  /api/cluster/nodes/live — живой статус из реестра monitor_nodes в БД
POST /api/cluster/nodes/live — живой статус, топология от фронтенда (override)

Все узлы опрашиваются параллельно.
ivamail_backend / ivamail_frontend → CMD SystemInfo + SSH OS probe
nfs / haproxy / monitoring        → SSH OS probe only
"""

import asyncio
import json
import logging
import socket
from datetime import datetime
from typing import Any, Dict, List, Optional

import paramiko
from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..infrastructure.cmd_client import CMDClient, CMDError, CMDConnectionError, CMDAuthError
from ..models.db_models import DeploymentRun, MonitorNode, get_db_engine, init_db

logger = logging.getLogger(__name__)

router = APIRouter()

_CMD_TIMEOUT = 8.0
_SSH_CONNECT_TIMEOUT = 8
_SSH_CMD_TIMEOUT = 5
_SSH_FULL_TIMEOUT = 10

_DEFAULT_CMD_USER = "admin"
_DEFAULT_CMD_PASS = "admin"


# ─── Request / Response models ───────────────────────────────────

class NodeMeta(BaseModel):
    ip: str
    hostname: str
    role: str


class LiveStatusRequest(BaseModel):
    cmd_nodes: List[NodeMeta] = []
    ssh_nodes: List[NodeMeta] = []
    cmd_user: str = _DEFAULT_CMD_USER
    cmd_password: str = _DEFAULT_CMD_PASS


class LiveNodeInfo(BaseModel):
    ip: str
    hostname: str
    role: str
    check_type: str          # "cmd" | "ssh"

    online: bool
    error: Optional[str] = None

    # OS fields (SSH probe)
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    os_pretty: Optional[str] = None

    # CMD-only fields
    version: Optional[str] = None
    uptime: Optional[str] = None
    load: Optional[float] = None
    connections: Optional[int] = None
    cluster_status: Optional[str] = None
    raw_info: Optional[Dict[str, Any]] = None

    checked_at: datetime


class LiveClusterStatus(BaseModel):
    nodes: List[LiveNodeInfo]
    total: int
    online: int
    offline: int
    checked_at: datetime


# ─── DB session helper ────────────────────────────────────────────

def _get_session() -> Session:
    engine = get_db_engine(settings.get_database_sync_url())
    init_db(engine)
    return Session(engine)


# ─── SSH OS probe (paramiko, sync → executor) ────────────────────

def _parse_os_release(output: str) -> Dict[str, Any]:
    """Парсит вывод cat /etc/os-release."""
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
    """SSH OS probe — cat /etc/os-release через paramiko в executor."""

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


# ─── CMD SystemInfo check ────────────────────────────────────────

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


def _extract_fields(info: Dict[str, Any]) -> Dict[str, Any]:
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


async def _check_cmd(node: NodeMeta, cmd_user: str, cmd_password: str) -> LiveNodeInfo:
    now = datetime.utcnow()

    async def _do() -> LiveNodeInfo:
        async with CMDClient(node.ip, settings.ivamail_port, timeout=_CMD_TIMEOUT) as cmd:
            await cmd.authenticate(cmd_user, cmd_password)
            resp = await cmd.system_info()
            if not resp.ok:
                return LiveNodeInfo(
                    ip=node.ip, hostname=node.hostname, role=node.role,
                    check_type="cmd", online=False,
                    error=f"SystemInfo код {resp.code}: {resp.text[:200]}",
                    checked_at=now,
                )
            raw = _parse_system_info(resp.text)
            fields = _extract_fields(raw)
            return LiveNodeInfo(
                ip=node.ip, hostname=node.hostname, role=node.role,
                check_type="cmd", online=True,
                version=fields["version"],
                uptime=fields["uptime"],
                load=fields["load"],
                connections=fields["connections"],
                cluster_status=fields["cluster_status"],
                raw_info=raw or None,
                checked_at=now,
            )

    try:
        return await asyncio.wait_for(_do(), timeout=_CMD_TIMEOUT + 2)
    except asyncio.TimeoutError:
        return LiveNodeInfo(
            ip=node.ip, hostname=node.hostname, role=node.role,
            check_type="cmd", online=False,
            error=f"Таймаут: CMD порт {settings.ivamail_port} не отвечает",
            checked_at=now,
        )
    except (CMDConnectionError, CMDAuthError, CMDError) as e:
        return LiveNodeInfo(
            ip=node.ip, hostname=node.hostname, role=node.role,
            check_type="cmd", online=False, error=str(e), checked_at=now,
        )
    except Exception as e:
        logger.exception(f"[Cluster] CMD probe {node.ip}")
        return LiveNodeInfo(
            ip=node.ip, hostname=node.hostname, role=node.role,
            check_type="cmd", online=False, error=str(e), checked_at=now,
        )


async def _check_ssh(node: NodeMeta) -> LiveNodeInfo:
    """Старый SSH TCP-check для обратной совместимости с POST-эндпоинтом."""
    now = datetime.utcnow()
    _SSH_TIMEOUT = 5.0
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(node.ip, 22),
            timeout=_SSH_TIMEOUT,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return LiveNodeInfo(
            ip=node.ip, hostname=node.hostname, role=node.role,
            check_type="ssh", online=True, checked_at=now,
        )
    except asyncio.TimeoutError:
        return LiveNodeInfo(
            ip=node.ip, hostname=node.hostname, role=node.role,
            check_type="ssh", online=False,
            error=f"Таймаут ({_SSH_TIMEOUT}s): SSH не отвечает",
            checked_at=now,
        )
    except Exception as e:
        return LiveNodeInfo(
            ip=node.ip, hostname=node.hostname, role=node.role,
            check_type="ssh", online=False,
            error=f"SSH недоступен: {e}",
            checked_at=now,
        )


# ─── Probe a MonitorNode from DB ─────────────────────────────────

async def _probe_monitor_node(node: MonitorNode) -> LiveNodeInfo:
    """Опрашивает ноду из реестра monitor_nodes."""
    now = datetime.utcnow()
    hostname = node.hostname or node.ip
    role = node.node_type
    is_ivamail = node.node_type in ("ivamail_backend", "ivamail_frontend")

    if is_ivamail:
        cmd_task_meta = NodeMeta(ip=node.ip, hostname=hostname, role=role)

        ssh_result, cmd_result = await asyncio.gather(
            _probe_ssh_os(node.ip, node.ssh_user, node.ssh_password, node.ssh_key_path, node.ssh_port),
            _check_cmd(cmd_task_meta, node.cmd_user or _DEFAULT_CMD_USER, node.cmd_password or _DEFAULT_CMD_PASS),
        )

        # CMD determines online status; SSH gives OS info as bonus
        online = cmd_result.online
        error = cmd_result.error if not online else None

        return LiveNodeInfo(
            ip=node.ip,
            hostname=hostname,
            role=role,
            check_type="cmd",
            online=online,
            error=error,
            os_name=ssh_result.get("os_name"),
            os_version=ssh_result.get("os_version"),
            os_pretty=ssh_result.get("os_pretty"),
            version=cmd_result.version,
            uptime=cmd_result.uptime,
            load=cmd_result.load,
            connections=cmd_result.connections,
            cluster_status=cmd_result.cluster_status,
            raw_info=cmd_result.raw_info,
            checked_at=now,
        )
    else:
        ssh_result = await _probe_ssh_os(node.ip, node.ssh_user, node.ssh_password, node.ssh_key_path, node.ssh_port)
        return LiveNodeInfo(
            ip=node.ip,
            hostname=hostname,
            role=role,
            check_type="ssh",
            online=ssh_result.get("online", False),
            error=ssh_result.get("error"),
            os_name=ssh_result.get("os_name"),
            os_version=ssh_result.get("os_version"),
            os_pretty=ssh_result.get("os_pretty"),
            checked_at=now,
        )


# ─── Core probe logic (POST / legacy) ───────────────────────────

async def _run_probes(
    cmd_nodes: List[NodeMeta],
    ssh_nodes: List[NodeMeta],
    cmd_user: str,
    cmd_password: str,
) -> LiveClusterStatus:
    tasks = (
        [_check_cmd(n, cmd_user, cmd_password) for n in cmd_nodes] +
        [_check_ssh(n) for n in ssh_nodes]
    )
    if not tasks:
        now = datetime.utcnow()
        return LiveClusterStatus(nodes=[], total=0, online=0, offline=0, checked_at=now)

    results: List[LiveNodeInfo] = await asyncio.gather(*tasks)
    now = datetime.utcnow()
    return LiveClusterStatus(
        nodes=results,
        total=len(results),
        online=sum(1 for r in results if r.online),
        offline=sum(1 for r in results if not r.online),
        checked_at=now,
    )


# ─── GET endpoint — читает из monitor_nodes ──────────────────────

@router.get(
    "/nodes/live",
    summary="Живой статус узлов из реестра monitor_nodes",
    response_model=LiveClusterStatus,
)
async def get_live_cluster_status() -> LiveClusterStatus:
    """
    Читает все ноды из таблицы monitor_nodes и опрашивает их параллельно.
    ivamail_* → CMD SystemInfo + SSH OS probe (параллельно).
    nfs / haproxy / monitoring → SSH OS probe (с парсингом /etc/os-release).
    """
    session = _get_session()
    try:
        db_nodes = (
            session.query(MonitorNode)
            .order_by(MonitorNode.sort_order, MonitorNode.id)
            .all()
        )
    finally:
        session.close()

    if not db_nodes:
        now = datetime.utcnow()
        return LiveClusterStatus(nodes=[], total=0, online=0, offline=0, checked_at=now)

    results: List[LiveNodeInfo] = await asyncio.gather(
        *[_probe_monitor_node(n) for n in db_nodes]
    )
    now = datetime.utcnow()
    return LiveClusterStatus(
        nodes=results,
        total=len(results),
        online=sum(1 for r in results if r.online),
        offline=sum(1 for r in results if not r.online),
        checked_at=now,
    )


# ─── POST endpoint — override от фронтенда ───────────────────────

@router.post(
    "/nodes/live",
    summary="Живой статус узлов кластера (топология от фронтенда)",
    response_model=LiveClusterStatus,
)
async def post_live_cluster_status(body: LiveStatusRequest) -> LiveClusterStatus:
    """
    Принимает списки нод от фронтенда и проверяет их параллельно.
    cmd_nodes → CMD SystemInfo (порт 106)
    ssh_nodes → SSH TCP connect (порт 22)
    """
    return await _run_probes(
        body.cmd_nodes, body.ssh_nodes, body.cmd_user, body.cmd_password
    )
