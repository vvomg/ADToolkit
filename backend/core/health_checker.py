"""
PHASE 4A: Health Checker

Проверяет состояние всех узлов кластера после установки.
Каждая проверка — независима, результат пишется в HealthCheckResult.

Типы проверок:
  ssh        — базовая доступность по SSH
  ivamail    — systemctl status ivamail (active/running)
  postgresql — pg_isready на database_server
  nfs        — mountpoint -q на каждом бэкенде
  cmd_ping   — CMD:PING через TCP 106
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from ..infrastructure.ssh_manager import SSHManager
from ..infrastructure.cmd_client import CMDClient, CMDConnectionError
from ..models.schemas import ClusterInput, ServerConfig
from ..models.db_models import HealthCheckResult, DeploymentLog
from ..core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Результат одной проверки."""
    server_ip: str
    server_hostname: str
    check_type: str
    success: bool
    details: dict = field(default_factory=dict)
    error_message: Optional[str] = None

    def to_db_model(self, deployment_id: str) -> HealthCheckResult:
        return HealthCheckResult(
            deployment_id=deployment_id,
            server_ip=self.server_ip,
            server_hostname=self.server_hostname,
            check_type=self.check_type,
            success=self.success,
            details=self.details,
            error_message=self.error_message,
        )


class HealthChecker:
    """
    Набор проверок здоровья кластера IVA Mail.

    Все проверки выполняются параллельно в рамках одного типа,
    но типы проверок последовательны (SSH → ivamail → pg → nfs → cmd).
    """

    def __init__(self, ssh: SSHManager):
        self._ssh = ssh

    # ─────────────────────────────────────────────────────────────────
    # Entry point
    # ─────────────────────────────────────────────────────────────────

    async def run_all(self, cluster: ClusterInput) -> List[CheckResult]:
        """Запускает все проверки и возвращает полный список результатов."""
        results: List[CheckResult] = []

        all_servers = list(cluster.backends)
        all_servers.append(cluster.database_server)
        if cluster.nfs_server:
            all_servers.append(cluster.nfs_server)
        if cluster.frontends:
            all_servers.extend(cluster.frontends)

        # ── SSH ──
        logger.info("[Health] Проверка SSH доступности всех узлов...")
        ssh_results = await asyncio.gather(
            *[self._check_ssh(s) for s in all_servers],
            return_exceptions=True,
        )
        for r in ssh_results:
            if isinstance(r, Exception):
                logger.error(f"[Health] SSH check exception: {r}")
            else:
                results.append(r)

        # ── IVA Mail service ──
        logger.info("[Health] Проверка статуса ivamail на бэкендах...")
        ivamail_results = await asyncio.gather(
            *[self._check_ivamail_service(b) for b in cluster.backends],
            return_exceptions=True,
        )
        for r in ivamail_results:
            if not isinstance(r, Exception):
                results.append(r)

        # ── PostgreSQL ──
        logger.info("[Health] Проверка PostgreSQL...")
        pg_result = await self._check_postgresql(cluster.database_server)
        results.append(pg_result)

        # ── NFS mount на бэкендах ──
        logger.info("[Health] Проверка NFS монтирования на бэкендах...")
        nfs_results = await asyncio.gather(
            *[self._check_nfs_mount(b, cluster.nfs_mount_point) for b in cluster.backends],
            return_exceptions=True,
        )
        for r in nfs_results:
            if not isinstance(r, Exception):
                results.append(r)

        # ── CMD PING на бэкендах ──
        logger.info("[Health] CMD:PING на бэкендах...")
        cmd_results = await asyncio.gather(
            *[self._check_cmd_ping(b) for b in cluster.backends],
            return_exceptions=True,
        )
        for r in cmd_results:
            if not isinstance(r, Exception):
                results.append(r)

        passed = sum(1 for r in results if r.success)
        total = len(results)
        logger.info(f"[Health] Итого: {passed}/{total} проверок прошло")
        return results

    # ─────────────────────────────────────────────────────────────────
    # Individual checks
    # ─────────────────────────────────────────────────────────────────

    async def _check_ssh(self, server: ServerConfig) -> CheckResult:
        """Базовая проверка SSH: echo ping."""
        try:
            ok, msg = await self._ssh.check_connectivity(server)
            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="ssh",
                success=ok,
                details={"message": msg},
                error_message=None if ok else msg,
            )
        except Exception as e:
            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="ssh",
                success=False,
                error_message=str(e),
            )

    async def _check_ivamail_service(self, server: ServerConfig) -> CheckResult:
        """Проверяет что ivamail.service в состоянии active(running)."""
        try:
            result = await self._ssh.execute_command(
                server, f"systemctl is-active {settings.ivamail_systemd_service}"
            )
            active = result.stdout.strip() == "active"

            # Дополнительно: uptime сервиса
            uptime_result = await self._ssh.execute_command(
                server,
                f"systemctl show {settings.ivamail_systemd_service} "
                f"--property=ActiveEnterTimestamp --value"
            )

            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="ivamail",
                success=active,
                details={
                    "systemctl_status": result.stdout.strip(),
                    "active_since": uptime_result.stdout.strip(),
                },
                error_message=None if active else (
                    f"Сервис не активен: {result.stdout.strip()}"
                ),
            )
        except Exception as e:
            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="ivamail",
                success=False,
                error_message=str(e),
            )

    async def _check_postgresql(self, server: ServerConfig) -> CheckResult:
        """pg_isready на database_server."""
        try:
            result = await self._ssh.execute_command(
                server,
                f"pg_isready -h 127.0.0.1 -p {settings.postgres_port} -U ivamail"
            )
            ok = result.success

            # Дополнительно: версия PostgreSQL
            ver_result = await self._ssh.execute_command(
                server, "psql -U postgres -tAc 'SELECT version();' 2>/dev/null | head -1"
            )

            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="postgresql",
                success=ok,
                details={
                    "pg_isready": result.stdout.strip() or result.stderr.strip(),
                    "version": ver_result.stdout.strip(),
                },
                error_message=None if ok else result.stderr.strip(),
            )
        except Exception as e:
            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="postgresql",
                success=False,
                error_message=str(e),
            )

    async def _check_nfs_mount(
        self, server: ServerConfig, mount_point: str
    ) -> CheckResult:
        """Проверяет что NFS шара смонтирована и доступна для записи."""
        try:
            mount_check = await self._ssh.execute_command(
                server, f"mountpoint -q {mount_point} && echo mounted || echo not_mounted"
            )
            mounted = "mounted" in mount_check.stdout

            write_ok = False
            if mounted:
                test_file = f"{mount_point}/.health_check_{server.ip}"
                write_result = await self._ssh.execute_command(
                    server, f"touch {test_file} && rm {test_file} && echo ok || echo fail"
                )
                write_ok = "ok" in write_result.stdout

            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="nfs",
                success=mounted and write_ok,
                details={
                    "mount_point": mount_point,
                    "mounted": mounted,
                    "write_ok": write_ok,
                },
                error_message=None if (mounted and write_ok) else (
                    f"NFS не смонтирован на {mount_point}" if not mounted
                    else "NFS смонтирован но недоступен для записи"
                ),
            )
        except Exception as e:
            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="nfs",
                success=False,
                error_message=str(e),
            )

    async def _check_cmd_ping(self, server: ServerConfig) -> CheckResult:
        """CMD:PING через TCP порт 106 — без аутентификации."""
        try:
            # Подключаемся, читаем баннер — этого достаточно для ping
            client = CMDClient(server.ip, settings.ivamail_port, timeout=10.0)
            banner = await asyncio.wait_for(client.connect(), timeout=10.0)
            await client.close()

            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="cmd_ping",
                success=True,
                details={"banner": banner[:120]},
            )
        except CMDConnectionError as e:
            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="cmd_ping",
                success=False,
                error_message=str(e),
            )
        except Exception as e:
            return CheckResult(
                server_ip=server.ip,
                server_hostname=server.hostname,
                check_type="cmd_ping",
                success=False,
                error_message=str(e),
            )

    # ─────────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────────

    def build_summary(self, results: List[CheckResult]) -> dict:
        """Строит сводку результатов по типам проверок."""
        by_type: dict[str, dict] = {}
        for r in results:
            if r.check_type not in by_type:
                by_type[r.check_type] = {"passed": 0, "failed": 0, "servers": []}
            entry = by_type[r.check_type]
            if r.success:
                entry["passed"] += 1
            else:
                entry["failed"] += 1
            entry["servers"].append({
                "ip": r.server_ip,
                "hostname": r.server_hostname,
                "ok": r.success,
                "error": r.error_message,
            })

        total = len(results)
        passed = sum(1 for r in results if r.success)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "all_ok": passed == total,
            "by_type": by_type,
        }
