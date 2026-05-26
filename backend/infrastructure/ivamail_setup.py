"""
PHASE 3C: IVA Mail Setup

Устанавливает пакет IVA Mail на все узлы кластера.
Поддерживает три источника дистрибутива:
  - local_file  : файл на машине toolkit → копируем по SFTP
  - url         : toolkit скачивает → копируем по SFTP
  - server_path : файл уже на сервере → устанавливаем напрямую

Формат пакета определяется автоматически по расширению (.deb/.rpm)
или задаётся вручную.
"""

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import List, Optional

import httpx

from ..infrastructure.ssh_manager import SSHManager, SSHCommandResult
from ..infrastructure.config_generator import ConfigGenerator
from ..models.schemas import ClusterInput, ServerConfig, PackageSource, PackageConfig, PackageFormat, PackageSourceType
from ..core.config import settings

logger = logging.getLogger(__name__)


def extract_version_from_filename(filename: str) -> str:
    """
    Извлекает версию из имени файла пакета.
    ivamail-26.05.6920.x86_64.deb -> ivamail-26.05.6920
    ivamail-26.05.6920-1.x86_64.rpm -> ivamail-26.05.6920
    """
    import re
    name = filename
    # Убираем расширение
    for ext in ['.deb', '.rpm']:
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    # Убираем платформу: .x86_64, .amd64, .arm64, -1.x86_64 и т.п.
    name = re.sub(r'[-_.](x86_64|amd64|arm64|i386|noarch).*$', '', name)
    # Убираем revision rpm: -1 в конце
    name = re.sub(r'-\d+$', '', name)
    return name

IVAMAIL_SERVICE   = "ivamail"
IVAMAIL_CONFIG_DIR = "/etc/ivamail"
IVAMAIL_DATA_DIR   = "/var/ivamail"
REMOTE_TMP_DIR     = "/tmp/ivamail_pkg"


class IvamailSetup:
    """
    Установка IVA Mail на все узлы кластера.

    Стратегия:
    - Параллельная установка на все бэкенды (asyncio.gather)
    - Последовательная пост-конфигурация (primary → secondary)
    - Поддержка трёх источников дистрибутива
    """

    def __init__(self, ssh: SSHManager):
        self._ssh = ssh

    async def setup(self, cluster: ClusterInput, package_config: Optional[PackageConfig] = None) -> List[str]:
        """Полная установка IVA Mail на все узлы."""
        logs: List[str] = []
        logger.info(f"[IVA] Установка IVA Mail {cluster.ivamail_version} на кластер")
        logs.append(f"IVA Mail setup: версия={cluster.ivamail_version}, узлов={len(cluster.backends)}")

        all_nodes = list(cluster.backends) + list(cluster.frontends or [])

        ver = (cluster.ivamail_version or "").strip()
        for server in all_nodes:
            src = package_config.get_source_for(server.ip) if package_config else None
            if src is not None and src.is_install_ready():
                continue
            if not ver:
                raise RuntimeError(
                    "[IVA] Пакет IVA Mail не задан: в репозиториях Debian/Ubuntu по умолчанию "
                    "нет пакета «ivamail». Укажите в кластере package_config "
                    "(локальный файл, URL или путь .deb/.rpm на сервере) "
                    f"для узла {server.hostname} ({server.ip}), либо задайте ivamail_version "
                    "и подключите на хостах репозиторий вендора IVA Mail (apt/dnf)."
                )

        # ── Параллельная установка на все ноды (backends + frontends) ──
        logs.append(f"Параллельная установка пакета на все узлы ({len(all_nodes)})...")
        tasks = [
            self._install_on_server(server, cluster, package_config)
            for server in all_nodes
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for server, result in zip(all_nodes, results):
            if isinstance(result, Exception):
                msg = f"ОШИБКА установки на {server.hostname}: {result}"
                logs.append(msg)
                raise RuntimeError(msg)
            logs.extend(result)

        # ── Конфигурация ──
        primary = cluster.backends[0]
        logs.append(f"Конфигурация Backend 1 ({primary.hostname}) как Dispatcher-кандидата...")
        logs.extend(await self._configure_primary(primary, cluster))

        for backend in cluster.backends[1:]:
            logs.extend(await self._configure_secondary(backend, cluster))

        for frontend in (cluster.frontends or []):
            logs.extend(await self._configure_frontend(frontend, cluster))

        logs.append("IVA Mail установлен на все узлы ✓")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Install on single server
    # ─────────────────────────────────────────────────────────────────

    async def _install_on_server(
        self,
        server: ServerConfig,
        cluster: ClusterInput,
        package_config: Optional[PackageConfig],
    ) -> List[str]:
        """Установка на один сервер — выбирает стратегию по типу источника."""
        logs: List[str] = []

        source = package_config.get_source_for(server.ip) if package_config else None

        if source is None:
            # Fallback: apt/dnf по версии (старое поведение)
            logs.extend(await self._install_from_repo(server, cluster.ivamail_version))
        else:
            pkg_format = source.detect_format()
            logs.append(f"[{server.hostname}] Источник: {source.source_type.value}, формат: {pkg_format.value}")

            # После шага «Скачать по URL» в UI пакет уже на машине toolkit (local_path);
            # не качаем повторно с URL на каждую ноду.
            if source.source_type == PackageSourceType.URL and source.local_path:
                logs.extend(await self._install_from_local(server, source, pkg_format))
            elif source.source_type == PackageSourceType.LOCAL_FILE:
                logs.extend(await self._install_from_local(server, source, pkg_format))
            elif source.source_type == PackageSourceType.URL:
                logs.extend(await self._install_from_url(server, source, pkg_format))
            elif source.source_type == PackageSourceType.SERVER_PATH:
                logs.extend(await self._install_from_server_path(server, source, pkg_format))

        return logs

    # ─────────────────────────────────────────────────────────────────
    # Strategy: local_file → SFTP → install
    # ─────────────────────────────────────────────────────────────────

    async def _install_from_local(
        self,
        server: ServerConfig,
        source: PackageSource,
        pkg_format: PackageFormat,
    ) -> List[str]:
        """Копирует файл с машины toolkit на сервер по SFTP, затем устанавливает."""
        logs: List[str] = []
        local_path = source.local_path

        if not local_path or not Path(local_path).exists():
            raise RuntimeError(f"[IVA] Файл не найден на машине toolkit: {local_path}")

        filename = Path(local_path).name
        remote_path = f"{REMOTE_TMP_DIR}/{filename}"

        # Создаём tmp директорию на сервере (chmod 777 нужен для SFTP, работающего от непривилегированного пользователя)
        await self._run(server, f"mkdir -p {REMOTE_TMP_DIR} && chmod 777 {REMOTE_TMP_DIR}")

        # Копируем по SFTP
        logs.append(f"[{server.hostname}] Копирование {filename} по SFTP...")
        await self._ssh.upload_file(server, local_path, remote_path)
        logs.append(f"[{server.hostname}] Файл скопирован → {remote_path}")

        # Устанавливаем
        install_logs = await self._install_package_file(server, remote_path, pkg_format)
        logs.extend(install_logs)

        # Чистим tmp
        await self._run(server, f"rm -f {remote_path}")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Strategy: url → toolkit downloads → SFTP → install
    # ─────────────────────────────────────────────────────────────────

    async def _install_from_url(
        self,
        server: ServerConfig,
        source: PackageSource,
        pkg_format: PackageFormat,
    ) -> List[str]:
        """Toolkit скачивает пакет по URL, затем копирует на сервер по SFTP."""
        logs: List[str] = []
        url = source.url

        if not url:
            raise RuntimeError("[IVA] URL не указан")

        filename = source.get_filename()
        logs.append(f"[{server.hostname}] Скачивание пакета: {url}")

        # Скачиваем во временный файл на машине toolkit
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                async with client.stream('GET', url) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"HTTP {response.status_code} при скачивании {url}")
                    total = 0
                    with open(tmp_path, 'wb') as f:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            f.write(chunk)

            logs.append(f"[{server.hostname}] Скачано {total // 1024} KB, копирование по SFTP...")

            # Теперь как local_file
            source_local = PackageSource(
                source_type=PackageSourceType.LOCAL_FILE,
                local_path=tmp_path,
                filename=filename,
                package_format=source.package_format,
            )
            install_logs = await self._install_from_local(server, source_local, pkg_format)
            logs.extend(install_logs)

        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return logs

    # ─────────────────────────────────────────────────────────────────
    # Strategy: server_path → install directly
    # ─────────────────────────────────────────────────────────────────

    async def _install_from_server_path(
        self,
        server: ServerConfig,
        source: PackageSource,
        pkg_format: PackageFormat,
    ) -> List[str]:
        """Файл уже на сервере — устанавливаем напрямую."""
        logs: List[str] = []
        path = source.server_path

        if not path:
            raise RuntimeError("[IVA] Путь на сервере не указан")

        # Проверяем что файл существует
        check = await self._ssh.execute_command(server, f"test -f '{path}' && echo exists || echo missing")
        if "missing" in check.stdout:
            raise RuntimeError(f"[IVA] Файл не найден на {server.hostname}: {path}")

        logs.append(f"[{server.hostname}] Файл найден на сервере: {path}")
        install_logs = await self._install_package_file(server, path, pkg_format)
        logs.extend(install_logs)
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Strategy: fallback — apt/dnf по версии
    # ─────────────────────────────────────────────────────────────────

    async def _install_from_repo(self, server: ServerConfig, version: str) -> List[str]:
        """Старое поведение: установка через apt/dnf по версии пакета."""
        logs: List[str] = []
        version = (version or "").strip()

        installed = await self._ssh.execute_command(
            server, f"dpkg -l ivamail 2>/dev/null | grep '^ii' | awk '{{print $3}}'"
        )
        current_ver = installed.stdout.strip()

        # Только при явной версии: совпадение = уже установлено
        if version and current_ver == version:
            logs.append(f"[{server.hostname}] ivamail=={version} уже установлен — пропускаем")
            return logs

        # Версия в UI не задана, но пакет уже есть — не трогаем apt
        if not version and current_ver:
            logs.append(
                f"[{server.hostname}] ivamail={current_ver} уже установлен "
                f"(ivamail_version пуст — пропускаем apt)"
            )
            return logs

        await self._ssh.wait_dpkg_lock(server)
        await self._run(server, "apt-get update -qq")

        if version:
            result = await self._run(
                server, f"DEBIAN_FRONTEND=noninteractive apt-get install -y ivamail={version}"
            )
            if not result.success:
                fallback = await self._run(
                    server, "DEBIAN_FRONTEND=noninteractive apt-get install -y ivamail"
                )
                if not fallback.success:
                    raise RuntimeError(f"[IVA] {server.ip}: установка ivamail: {fallback.stderr}")
                logs.append(f"[{server.hostname}] ivamail установлен (последняя доступная версия)")
            else:
                logs.append(f"[{server.hostname}] ivamail=={version} установлен через apt")
        else:
            result = await self._run(
                server, "DEBIAN_FRONTEND=noninteractive apt-get install -y ivamail"
            )
            if not result.success:
                raise RuntimeError(
                    f"[IVA] {server.ip}: apt install ivamail: {result.stderr}. "
                    "Укажите ivamail_version или package_config (файл/URL/путь на сервере)."
                )
            logs.append(f"[{server.hostname}] ivamail установлен через apt (версия из репозитория)")

        await self._run(server, f"systemctl enable {IVAMAIL_SERVICE}")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Package installation (deb/rpm)
    # ─────────────────────────────────────────────────────────────────

    async def _install_package_file(
        self,
        server: ServerConfig,
        remote_path: str,
        pkg_format: PackageFormat,
    ) -> List[str]:
        """Устанавливает пакет из файла на сервере (deb или rpm)."""
        logs: List[str] = []

        if pkg_format == PackageFormat.DEB:
            # dpkg -i, затем apt-get install -f для зависимостей
            result = await self._run(server, f"dpkg -i '{remote_path}' 2>&1 || true")
            logs.append(f"[{server.hostname}] dpkg -i: exit={result.exit_code}")

            # Исправляем зависимости
            fix = await self._run(server, "DEBIAN_FRONTEND=noninteractive apt-get install -f -y")
            if not fix.success:
                raise RuntimeError(f"[IVA] {server.ip}: apt-get install -f: {fix.stderr}")
            logs.append(f"[{server.hostname}] Зависимости установлены (apt-get install -f)")

        elif pkg_format == PackageFormat.RPM:
            # Пробуем dnf, затем yum, затем rpm -i
            installed_via = None
            for pkg_mgr in ["dnf", "yum"]:
                check = await self._ssh.execute_command(server, f"which {pkg_mgr} 2>/dev/null")
                if check.success:
                    result = await self._run(server, f"{pkg_mgr} install -y '{remote_path}'")
                    if result.success:
                        logs.append(f"[{server.hostname}] Установлен через {pkg_mgr}")
                        installed_via = pkg_mgr
                        break
                    logger.warning(
                        f"[IVA] {server.ip}: {pkg_mgr} install не удался "
                        f"(exit={result.exit_code}), пробуем следующий"
                    )
            if not installed_via:
                # fallback: rpm -i
                result = await self._run(server, f"rpm -i '{remote_path}'")
                if not result.success:
                    raise RuntimeError(f"[IVA] {server.ip}: rpm -i: {result.stderr}")
                logs.append(f"[{server.hostname}] Установлен через rpm -i")

        await self._run(server, f"systemctl enable {IVAMAIL_SERVICE}")
        logs.append(f"[{server.hostname}] ivamail установлен, служба включена в автозапуск ✓")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Configuration (без изменений)
    # ─────────────────────────────────────────────────────────────────

    async def _configure_primary(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []
        db = cluster.database_server
        for d in [IVAMAIL_CONFIG_DIR, IVAMAIL_DATA_DIR, cluster.nfs_mount_point]:
            await self._run(server, f"mkdir -p {d}")
        config_lines = [
            f"# IVA Mail configuration — сгенерировано Deploy Toolkit",
            f"db_host={db.ip}", f"db_port={settings.postgres_port}",
            f"db_name=ivamail", f"db_user=ivamail",
            f"nfs_path={cluster.nfs_mount_point}",
            f"cluster_role=primary",
            f"cluster_backend_count={len(cluster.backends)}",
        ]
        escaped = "\n".join(config_lines).replace("'", "'\\''")
        result = await self._run(server, f"echo '{escaped}' > {IVAMAIL_CONFIG_DIR}/ivamail.conf")
        if not result.success:
            raise RuntimeError(f"[IVA] {server.ip}: запись конфига: {result.stderr}")
        logs.append(f"[{server.hostname}] Конфиг primary записан ✓")
        return logs

    async def _configure_frontend(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []
        db = cluster.database_server
        for d in [IVAMAIL_CONFIG_DIR, IVAMAIL_DATA_DIR]:
            await self._run(server, f"mkdir -p {d}")
        config_lines = [
            f"# IVA Mail configuration — сгенерировано Deploy Toolkit",
            f"db_host={db.ip}", f"db_port={settings.postgres_port}",
            f"db_name=ivamail", f"db_user=ivamail",
            f"cluster_role=frontend",
        ]
        escaped = "\n".join(config_lines).replace("'", "'\\''")
        result = await self._run(server, f"echo '{escaped}' > {IVAMAIL_CONFIG_DIR}/ivamail.conf")
        if not result.success:
            raise RuntimeError(f"[IVA] {server.ip}: запись конфига: {result.stderr}")
        logs.append(f"[{server.hostname}] Конфиг frontend записан ✓")
        return logs

    async def _configure_secondary(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []
        db = cluster.database_server
        for d in [IVAMAIL_CONFIG_DIR, IVAMAIL_DATA_DIR, cluster.nfs_mount_point]:
            await self._run(server, f"mkdir -p {d}")
        config_lines = [
            f"# IVA Mail configuration — сгенерировано Deploy Toolkit",
            f"db_host={db.ip}", f"db_port={settings.postgres_port}",
            f"db_name=ivamail", f"db_user=ivamail",
            f"nfs_path={cluster.nfs_mount_point}",
            f"cluster_role=secondary",
        ]
        escaped = "\n".join(config_lines).replace("'", "'\\''")
        result = await self._run(server, f"echo '{escaped}' > {IVAMAIL_CONFIG_DIR}/ivamail.conf")
        if not result.success:
            raise RuntimeError(f"[IVA] {server.ip}: запись конфига: {result.stderr}")
        logs.append(f"[{server.hostname}] Конфиг secondary записан ✓")
        return logs

    # ─────────────────────────────────────────────────────────────────

    async def _run(self, server: ServerConfig, cmd: str) -> SSHCommandResult:
        result = await self._ssh.execute_command(server, cmd)
        if not result.success:
            logger.warning(f"[IVA] [{server.ip}] exit={result.exit_code}: {cmd[:80]}")
        return result
