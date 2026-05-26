"""
PHASE 1.3: SSH Manager
"""

import asyncio
import logging
import os
import shlex
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

import paramiko
from paramiko import SSHClient, AutoAddPolicy

from ..models.schemas import ServerConfig

logger = logging.getLogger(__name__)


class SSHConnectionError(Exception):
    pass


@dataclass
class SSHCommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    server_ip: str
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def __repr__(self) -> str:
        status = "OK" if self.success else f"FAIL({self.exit_code})"
        return f"<SSHCommandResult [{status}] {self.server_ip}: {self.command[:50]}>"


class SSHManager:
    def __init__(self):
        self.connections: Dict[str, SSHClient] = {}
        self._executor = ThreadPoolExecutor(max_workers=10)
        logger.info("SSHManager инициализирован")

    def _make_key(self, server: ServerConfig) -> str:
        return f"{server.ip}:{server.ssh_port}"

    def _connect_sync(self, server: ServerConfig) -> SSHClient:
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        from ..core.config import settings as _settings
        connect_kwargs = {
            "hostname": server.ip,
            "port": server.ssh_port,
            "username": server.ssh_user,
            "timeout": _settings.ssh_connection_timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if server.ssh_key_path:
            connect_kwargs["key_filename"] = server.ssh_key_path
        elif server.ssh_password:
            connect_kwargs["password"] = server.ssh_password
        else:
            raise SSHConnectionError(f"Не указан ни пароль, ни путь к ключу для {server.ip}")

        for attempt in range(1, _settings.ssh_max_retries + 1):
            try:
                client.connect(**connect_kwargs)
                logger.info(f"[{server.ip}] SSH подключение установлено")
                return client
            except Exception as e:
                if attempt == _settings.ssh_max_retries:
                    raise SSHConnectionError(
                        f"Не удалось подключиться к {server.ip} после {_settings.ssh_max_retries} попыток: {e}"
                    ) from e
                delay = _settings.ssh_retry_delay * (2 ** (attempt - 1))
                logger.warning(f"[{server.ip}] Ошибка подключения (попытка {attempt}): {e}. Повтор через {delay}с")
                time.sleep(delay)
        raise SSHConnectionError(f"Не удалось подключиться к {server.ip}")

    def _get_or_create_connection(self, server: ServerConfig) -> SSHClient:
        key = self._make_key(server)
        client = self.connections.get(key)
        if client is not None:
            try:
                transport = client.get_transport()
                if transport and transport.is_active():
                    return client
            except Exception:
                pass
            self.connections.pop(key, None)
        client = self._connect_sync(server)
        self.connections[key] = client
        return client

    def _wrap_with_sudo(self, server: ServerConfig, command: str) -> str:
        """Оборачивает команду для выполнения с правами root через sudo -S."""
        if server.ssh_user == 'root':
            return command
        if not server.ssh_password:
            if command.strip().startswith('sudo'):
                return command
            return f"sudo -n {command}"
        safe_pass = server.ssh_password.replace("'", "'\\''")
        if command.strip().startswith('sudo'):
            # Убираем 'sudo' и передаём пароль через -S
            cmd_body = command.strip()[4:].lstrip()
            return f"echo '{safe_pass}' | sudo -S -p '' {cmd_body}"
        return f"echo '{safe_pass}' | sudo -S -p '' sh -c {shlex.quote(command)}"

    async def execute_command(
        self,
        server: ServerConfig,
        command: str,
        timeout: Optional[int] = None,
        use_sudo: bool = True,
    ) -> SSHCommandResult:
        from ..core.config import settings as _settings
        timeout = timeout or _settings.ssh_command_timeout
        actual_command = self._wrap_with_sudo(server, command) if use_sudo else command

        def _run():
            client = self._get_or_create_connection(server)
            start = time.time()
            stdin, stdout, stderr = client.exec_command(actual_command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            return SSHCommandResult(
                command=command,
                exit_code=exit_code,
                stdout=stdout.read().decode("utf-8", errors="replace").strip(),
                stderr=stderr.read().decode("utf-8", errors="replace").strip(),
                server_ip=server.ip,
                duration_seconds=round(time.time() - start, 2),
            )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(self._executor, _run)
        log_level = logging.DEBUG if result.success else logging.WARNING
        logger.log(log_level, f"[{server.ip}] [{result.exit_code}] {command[:80]} ({result.duration_seconds}s)")
        return result

    async def wait_dpkg_lock(self, server: ServerConfig, timeout: int = 120) -> None:
        """Ожидает освобождения блокировки dpkg перед apt-get операциями."""
        cmd = (
            f"timeout {timeout} sh -c '"
            "while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock "
            "/var/cache/apt/archives/lock >/dev/null 2>&1; do sleep 3; done'"
        )
        await self.execute_command(server, cmd)

    async def check_connectivity(self, server: ServerConfig) -> Tuple[bool, str]:
        try:
            result = await self.execute_command(server, "echo ping", timeout=10)
            if result.success and "ping" in result.stdout:
                return True, "OK"
            return False, f"Неожиданный ответ: {result.stdout}"
        except SSHConnectionError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Ошибка: {e}"

    async def upload_file(self, server: ServerConfig, local_path: str, remote_path: str) -> bool:
        def _upload():
            client = self._get_or_create_connection(server)
            sftp = client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _upload)
        logger.info(f"[{server.ip}] Файл загружен: {local_path} → {remote_path}")
        return True

    async def upload_file_sudo(
        self,
        server: ServerConfig,
        local_path: str,
        remote_path: str,
        owner: str = "root:root",
        mode: str = "644",
    ) -> bool:
        """Загружает файл через /tmp/ с последующим sudo mv."""
        tmp_path = f"/tmp/_ivamail_upload_{os.path.basename(remote_path)}"

        def _upload():
            client = self._get_or_create_connection(server)
            sftp = client.open_sftp()
            sftp.put(local_path, tmp_path)
            sftp.close()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _upload)
        logger.info(f"[{server.ip}] Файл загружен во tmp: {local_path} → {tmp_path}")

        move_cmd = f"mv '{tmp_path}' '{remote_path}' && chmod {mode} '{remote_path}' && chown {owner} '{remote_path}'"
        result = await self.execute_command(server, move_cmd)
        if not result.success:
            raise PermissionError(f"[{server.ip}] sudo mv не удался: {result.stderr}")

        logger.info(f"[{server.ip}] Файл перемещён: {tmp_path} → {remote_path}")
        return True

    async def download_file(self, server: ServerConfig, remote_path: str, local_path: str) -> bool:
        def _download():
            client = self._get_or_create_connection(server)
            sftp = client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _download)
        logger.info(f"[{server.ip}] Файл скачан: {remote_path} → {local_path}")
        return True

    def close_all(self) -> None:
        for key, client in list(self.connections.items()):
            try:
                client.close()
            except Exception as e:
                logger.warning(f"Ошибка при закрытии соединения {key}: {e}")
        self.connections.clear()

    def __del__(self):
        self.close_all()


ssh_manager = SSHManager()
