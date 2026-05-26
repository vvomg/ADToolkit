"""
PHASE 3B: NFS Setup

Настраивает NFS-шару на database-сервере и монтирует её на всех бэкендах.
Все операции идемпотентны.

Порядок шагов на NFS-сервере:
  1. Установка nfs-kernel-server
  2. Создание директории шары
  3. Загрузка /etc/exports (Jinja2)
  4. exportfs -ra + запуск службы

Порядок шагов на каждом бэкенде:
  1. Установка nfs-common
  2. Создание точки монтирования
  3. Монтирование (если ещё не смонтировано)
  4. Добавление в /etc/fstab (идемпотентно)
  5. Верификация записи
"""

import asyncio
import logging
from typing import List

from ..infrastructure.ssh_manager import SSHManager, SSHCommandResult
from ..infrastructure.config_generator import ConfigGenerator
from ..models.schemas import ClusterInput, ServerConfig
from ..core.config import settings

logger = logging.getLogger(__name__)

NFS_SERVER_PKG = "nfs-kernel-server"
NFS_CLIENT_PKG = "nfs-common"


class NFSSetup:
    """
    Настройка NFS: сервер (database_server) + клиенты (backends).

    Параллельно монтирует шару на всех бэкендах через asyncio.gather
    после того как сервер готов.
    """

    def __init__(self, ssh: SSHManager, cfg_gen: ConfigGenerator):
        self._ssh = ssh
        self._cfg = cfg_gen

    async def setup(self, cluster: ClusterInput) -> List[str]:
        """Полная настройка NFS. Возвращает лог шагов."""
        logs: List[str] = []
        nfs_server = cluster.nfs_server or cluster.database_server

        logger.info(f"[NFS] Начало настройки NFS сервера на {nfs_server.ip}")
        logs.append(f"NFS setup: сервер={nfs_server.hostname} ({nfs_server.ip})")

        # ── Фаза 1: Настройка NFS-сервера ──
        server_logs = await self._setup_server(nfs_server, cluster)
        logs.extend(server_logs)

        # ── Фаза 2: Монтирование на всех бэкендах параллельно ──
        logs.append(f"Монтирование NFS на {len(cluster.backends)} бэкенде(ах) параллельно...")
        tasks = [
            self._setup_client(backend, nfs_server, cluster)
            for backend in cluster.backends
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for backend, result in zip(cluster.backends, results):
            if isinstance(result, Exception):
                msg = f"ОШИБКА монтирования на {backend.hostname}: {result}"
                logs.append(msg)
                logger.error(f"[NFS] {msg}")
                raise RuntimeError(msg)
            logs.extend(result)

        logs.append("NFS настроен на всех узлах ✓")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Server setup
    # ─────────────────────────────────────────────────────────────────

    async def _setup_server(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []

        # Установка пакета
        pkg_check = await self._ssh.execute_command(
            server, f"dpkg -l {NFS_SERVER_PKG} | grep -q '^ii'"
        )
        if not pkg_check.success:
            await self._ssh.wait_dpkg_lock(server)
            result = await self._run(server, f"apt-get update -qq && "
                                     f"DEBIAN_FRONTEND=noninteractive apt-get install -y {NFS_SERVER_PKG}")
            if not result.success:
                raise RuntimeError(f"[NFS] Установка {NFS_SERVER_PKG}: {result.stderr}")
            logs.append(f"{NFS_SERVER_PKG} установлен")
        else:
            logs.append(f"{NFS_SERVER_PKG} уже установлен")

        # Создание директории шары
        share = cluster.nfs_share_path
        result = await self._run(server, f"mkdir -p {share} && chmod 777 {share}")
        if not result.success:
            raise RuntimeError(f"[NFS] Не удалось создать {share}: {result.stderr}")
        logs.append(f"Директория шары создана: {share}")

        # Загрузка /etc/exports
        exports_file = self._cfg.generate_nfs_exports(cluster)
        await self._ssh.upload_file_sudo(server, exports_file.local_path, exports_file.remote_path, owner="root:root", mode="644")
        logs.append(f"/etc/exports обновлён ({len(cluster.backends)} клиентов)")

        # Применение exports и запуск службы
        cmds = [
            "exportfs -ra",
            "systemctl restart nfs-kernel-server",
            "systemctl enable nfs-kernel-server",
        ]
        for cmd in cmds:
            result = await self._run(server, cmd)
            if not result.success:
                raise RuntimeError(f"[NFS] Ошибка '{cmd}': {result.stderr}")

        # Верификация
        result = await self._ssh.execute_command(server, f"exportfs -v | grep '{share}'")
        if not result.success:
            raise RuntimeError(f"[NFS] Шара {share} не экспортируется после настройки")
        logs.append(f"NFS сервер активен: {result.stdout.strip()}")

        return logs

    # ─────────────────────────────────────────────────────────────────
    # Client setup (один бэкенд)
    # ─────────────────────────────────────────────────────────────────

    async def _setup_client(
        self,
        backend: ServerConfig,
        nfs_server: ServerConfig,
        cluster: ClusterInput,
    ) -> List[str]:
        logs: List[str] = []
        # Точка монтирования: /mnt/mailshare (инструкция §5)
        mount_point = settings.nfs_client_mount_point
        share_path = cluster.nfs_share_path
        nfs_source = f"{nfs_server.ip}:{share_path}"
        # Симлинк /var/ivamail/Cluster → /mnt/mailshare (инструкция §5.1)
        ivamail_symlink = settings.nfs_ivamail_symlink

        # fstab строка без одинарных кавычек в пути (безопасный вариант)
        fstab_entry = (
            f"{nfs_source}  {mount_point}  nfs  "
            f"{settings.nfs_mount_options}  0  0"
        )

        # Установка nfs-common (клиентский пакет, НЕ nfs-kernel-server)
        pkg_check = await self._ssh.execute_command(
            backend, f"dpkg -l {NFS_CLIENT_PKG} | grep -q '^ii'"
        )
        if not pkg_check.success:
            await self._ssh.wait_dpkg_lock(backend)
            result = await self._run(
                backend,
                f"apt-get update -qq && "
                f"DEBIAN_FRONTEND=noninteractive apt-get install -y {NFS_CLIENT_PKG}"
            )
            if not result.success:
                raise RuntimeError(f"[NFS] {backend.ip}: установка {NFS_CLIENT_PKG}: {result.stderr}")
            logs.append(f"[{backend.hostname}] {NFS_CLIENT_PKG} установлен")
        else:
            logs.append(f"[{backend.hostname}] {NFS_CLIENT_PKG} уже установлен")

        # Создание точки монтирования
        result = await self._run(backend, f"mkdir -p {mount_point}")
        if not result.success:
            raise RuntimeError(f"[NFS] {backend.ip}: mkdir {mount_point}: {result.stderr}")
        logs.append(f"[{backend.hostname}] Точка монтирования: {mount_point}")

        # Монтирование (если ещё не смонтировано)
        already = await self._ssh.execute_command(backend, f"mountpoint -q {mount_point}")
        if already.success:
            logs.append(f"[{backend.hostname}] {mount_point} уже смонтирован")
        else:
            result = await self._run(
                backend,
                f"mount -t nfs -o {settings.nfs_mount_options} {nfs_source} {mount_point}"
            )
            if not result.success:
                raise RuntimeError(
                    f"[NFS] {backend.ip}: монтирование {nfs_source} → {mount_point}: {result.stderr}"
                )
            logs.append(f"[{backend.hostname}] Смонтировано: {nfs_source} → {mount_point}")

        # fstab (идемпотентно) — используем tee -a для безопасного добавления
        fstab_check = await self._ssh.execute_command(
            backend, f"grep -qF '{nfs_source}' /etc/fstab"
        )
        if fstab_check.success:
            logs.append(f"[{backend.hostname}] fstab: запись уже существует")
        else:
            result = await self._run(
                backend,
                f"printf '%s\\n' '{fstab_entry}' >> /etc/fstab"
            )
            if not result.success:
                raise RuntimeError(f"[NFS] {backend.ip}: запись в fstab: {result.stderr}")
            logs.append(f"[{backend.hostname}] fstab: запись добавлена")

        # Симлинк /var/ivamail/Cluster → /mnt/mailshare (инструкция §5.1)
        symlink_check = await self._ssh.execute_command(
            backend, f"test -L {ivamail_symlink}"
        )
        if symlink_check.success:
            logs.append(f"[{backend.hostname}] Симлинк {ivamail_symlink} уже существует")
        else:
            result = await self._run(
                backend,
                f"mkdir -p /var/ivamail && ln -sf {mount_point} {ivamail_symlink}"
            )
            if not result.success:
                raise RuntimeError(
                    f"[NFS] {backend.ip}: создание симлинка {ivamail_symlink}: {result.stderr}"
                )
            logs.append(f"[{backend.hostname}] Симлинк создан: {ivamail_symlink} → {mount_point}")

        # Права ivamail:ivamail для /var/ivamail (инструкция §5.1)
        await self._run(backend, "chown -R ivamail:ivamail /var/ivamail 2>/dev/null || true")
        logs.append(f"[{backend.hostname}] Права ivamail:ivamail на /var/ivamail установлены")

        # Верификация: тест записи через NFS
        test_file = f"{mount_point}/.ivamail_nfs_test_{backend.ip.replace('.', '_')}"
        write_test = await self._ssh.execute_command(
            backend, f"touch {test_file} && rm {test_file} && echo ok || echo fail"
        )
        if "ok" in write_test.stdout:
            logs.append(f"[{backend.hostname}] NFS запись/удаление файла — OK ✓")
        else:
            raise RuntimeError(f"[NFS] {backend.ip}: NFS шара недоступна для записи")

        return logs

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    async def _run(self, server: ServerConfig, cmd: str) -> SSHCommandResult:
        result = await self._ssh.execute_command(server, cmd)
        if not result.success:
            logger.warning(f"[NFS] [{server.ip}] exit={result.exit_code}: {cmd[:80]}")
        return result
