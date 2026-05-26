"""
PHASE 3A: PostgreSQL Setup
"""

import logging
from typing import List

from ..infrastructure.ssh_manager import SSHManager, SSHCommandResult
from ..infrastructure.config_generator import ConfigGenerator
from ..models.schemas import ClusterInput, ServerConfig

logger = logging.getLogger(__name__)

PG_SERVICE = "postgresql"
PG_USER    = "ivamail"
PG_DB      = "ivamail"


class PostgreSQLSetup:
    def __init__(self, ssh: SSHManager, cfg_gen: ConfigGenerator):
        self._ssh = ssh
        self._cfg = cfg_gen

    async def setup(self, cluster: ClusterInput) -> List[str]:
        server = cluster.database_server
        logs: List[str] = []
        logger.info(f"[PG] Начало настройки PostgreSQL на {server.ip}")
        logs.append(f"PostgreSQL setup на {server.hostname} ({server.ip})")

        steps = [
            ("Установка пакета",           self._install_package),
            ("Запуск PostgreSQL",           self._ensure_pg_running),
            ("Загрузка конфигурации",      self._upload_configs),
            ("Создание БД и пользователя", self._create_db_and_user),
            ("Перезапуск службы",          self._restart_service),
            ("Верификация подключения",    self._verify),
        ]

        for step_name, step_fn in steps:
            logger.info(f"[PG] {step_name}...")
            step_logs = await step_fn(server, cluster)
            for line in step_logs:
                logs.append(f"  [{step_name}] {line}")

        logs.append("PostgreSQL настроен успешно ✓")
        return logs

    async def _install_package(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs = []
        check = await self._ssh.execute_command(server, "dpkg -l postgresql | grep -q '^ii'")
        if not check.success:
            await self._ssh.wait_dpkg_lock(server)
            for cmd in [
                "apt-get update -qq",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-contrib",
            ]:
                result = await self._run(server, cmd)
                if not result.success:
                    raise RuntimeError(f"[PG] Ошибка установки: {result.stderr}")
            logs.append("postgresql установлен")
        else:
            logs.append("postgresql уже установлен")

        # Определяем версию
        pg_ver = await self._ssh.execute_command(
            server, "ls /usr/lib/postgresql/ 2>/dev/null | sort -V | tail -1 || echo 16"
        )
        pg_version = (pg_ver.stdout.strip() or "16").split()[0]

        # Проверяем и исправляем сломанный кластер (Invalid data directory)
        pg_ready = await self._ssh.execute_command(
            server, f"sudo -u postgres pg_isready -h 127.0.0.1 -p 5432 2>/dev/null"
        )
        if not pg_ready.success:
            # Проверяем что конфиг есть но data directory отсутствует
            conf_exists = await self._ssh.execute_command(
                server, f"test -f /etc/postgresql/{pg_version}/main/postgresql.conf"
            )
            data_exists = await self._ssh.execute_command(
                server, f"test -f /var/lib/postgresql/{pg_version}/main/PG_VERSION"
            )
            if conf_exists.success and not data_exists.success:
                logs.append(f"Обнаружен сломанный кластер — пересоздаём")
                await self._run(server, f"rm -rf /etc/postgresql/{pg_version}/main")
                await self._run(server, f"rm -rf /var/lib/postgresql/{pg_version}/main")
                result = await self._run(server, f"pg_createcluster {pg_version} main --start")
                if not result.success:
                    raise RuntimeError(f"[PG] Не удалось создать кластер: {result.stderr}")
                logs.append(f"Кластер PostgreSQL {pg_version}/main создан и запущен")

        return logs

    async def _ensure_pg_running(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        """Запускает PostgreSQL через pg_ctlcluster."""
        logs = []
        import asyncio

        # Определяем версию
        ver_result = await self._ssh.execute_command(
            server, "ls /usr/lib/postgresql/ 2>/dev/null | sort -V | tail -1 || echo 16"
        )
        pg_version = (ver_result.stdout.strip() or "16").split()[0]

        # Проверяем уже запущен ли
        ready = await self._ssh.execute_command(
            server, "sudo -u postgres pg_isready -h 127.0.0.1 -p 5432 2>/dev/null"
        )
        if ready.success:
            logs.append(f"PostgreSQL {pg_version} уже запущен")
            return logs

        # Запускаем через pg_ctlcluster
        await self._run(server, f"pg_ctlcluster {pg_version} main start 2>/dev/null || true")

        for attempt in range(15):
            await asyncio.sleep(2)
            ready = await self._ssh.execute_command(
                server, "sudo -u postgres pg_isready -h 127.0.0.1 -p 5432 2>/dev/null"
            )
            if ready.success:
                logs.append(f"PostgreSQL {pg_version} запущен (попытка {attempt+1})")
                return logs

        raise RuntimeError(f"[PG] PostgreSQL {pg_version} не запустился за 30 секунд")

    async def _upload_configs(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs = []

        # Определяем версию PostgreSQL
        pg_ver = await self._ssh.execute_command(
            server, "ls /usr/lib/postgresql/ 2>/dev/null | sort -V | tail -1 || echo 16"
        )
        pg_version = (pg_ver.stdout.strip() or "16").split()[0]

        pg_conf_path = f"/etc/postgresql/{pg_version}/main/postgresql.conf"
        pg_hba_path  = f"/etc/postgresql/{pg_version}/main/pg_hba.conf"

        pg_hba = self._cfg.generate_pg_hba(cluster)
        pg_conf = self._cfg.generate_postgresql_conf(cluster, pg_version=pg_version)
        pg_hba.remote_path = pg_hba_path
        pg_conf.remote_path = pg_conf_path

        for path in [pg_hba_path, pg_conf_path]:
            await self._ssh.execute_command(server, f"cp -n {path} {path}.bak 2>/dev/null || true")

        await self._ssh.upload_file_sudo(server, pg_hba.local_path, pg_hba.remote_path, owner="postgres:postgres", mode="640")
        await self._ssh.upload_file_sudo(server, pg_conf.local_path, pg_conf.remote_path, owner="postgres:postgres", mode="640")

        # Устанавливаем правильного владельца — postgres должен владеть конфигами
        for path in [pg_hba_path, pg_conf_path]:
            await self._run(server, f"chown postgres:postgres '{path}' && chmod 640 '{path}'")

        logs.append(f"pg_hba.conf → {pg_hba_path}")
        logs.append(f"postgresql.conf → {pg_conf_path}")
        return logs
    async def _create_db_and_user(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        """Создаёт пользователя и БД. Если уже существуют — использует существующие."""
        logs = []
        pg_password = (cluster.backends[0].ssh_password or "ivamail_secure_pass").replace("'", "").replace('"', '')

        # Пользователь
        user_exists = await self._ssh.execute_command(
            server, "sudo -u postgres psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='ivamail'\""
        )
        if "1" in user_exists.stdout:
            logs.append(f"Пользователь {PG_USER} уже существует — обновляем пароль")
            await self._run(
                server,
                f"sudo -u postgres psql -c \"ALTER USER {PG_USER} WITH PASSWORD '{pg_password}';\""
            )
        else:
            result = await self._run(server, f"sudo -u postgres createuser --createdb {PG_USER}")
            if not result.success:
                raise RuntimeError(f"[PG] Ошибка создания пользователя: {result.stderr}")
            await self._run(
                server,
                f"sudo -u postgres psql -c \"ALTER USER {PG_USER} WITH PASSWORD '{pg_password}';\""
            )
            logs.append(f"Пользователь {PG_USER} создан")

        # База данных
        db_exists = await self._ssh.execute_command(
            server, "sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname='ivamail'\""
        )
        if "1" in db_exists.stdout:
            logs.append(f"База данных {PG_DB} уже существует")
        else:
            result = await self._run(server, f"sudo -u postgres createdb -O {PG_USER} {PG_DB}")
            if not result.success:
                raise RuntimeError(f"[PG] Ошибка создания БД: {result.stderr}")
            logs.append(f"База данных {PG_DB} создана")

        return logs

    async def _restart_service(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs = []

        # Определяем версию PostgreSQL
        ver_result = await self._ssh.execute_command(
            server, "ls /usr/lib/postgresql/ 2>/dev/null | sort -V | tail -1 || echo 16"
        )
        pg_version = (ver_result.stdout.strip() or "16").split()[0]

        # Используем pg_ctlcluster напрямую — надёжнее чем systemctl
        result = await self._run(server, f"pg_ctlcluster {pg_version} main restart")
        if not result.success:
            # Fallback: stop + start
            await self._run(server, f"pg_ctlcluster {pg_version} main stop 2>/dev/null || true")
            import asyncio
            await asyncio.sleep(2)
            result2 = await self._run(server, f"pg_ctlcluster {pg_version} main start")
            if not result2.success:
                raise RuntimeError(f"[PG] Не удалось перезапустить PostgreSQL: {result2.stderr}")

        # Включаем автозапуск
        await self._run(server, f"systemctl enable postgresql@{pg_version}-main 2>/dev/null || true")
        logs.append(f"PostgreSQL {pg_version} перезапущен и включён в автозапуск")
        return logs

    async def _verify(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs = []
        result = await self._ssh.execute_command(
            server, "sudo -u postgres pg_isready -h 127.0.0.1 -p 5432"
        )
        if result.success:
            logs.append(f"PostgreSQL отвечает: {result.stdout}")
        else:
            raise RuntimeError(f"[PG] Верификация провалена: {result.stderr}")
        return logs

    async def _run(self, server: ServerConfig, cmd: str) -> SSHCommandResult:
        result = await self._ssh.execute_command(server, cmd)
        if not result.success:
            logger.warning(f"[PG] [{server.ip}] exit={result.exit_code}: {cmd[:80]}")
        return result
