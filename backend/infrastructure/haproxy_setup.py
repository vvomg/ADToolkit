"""
HAProxy Setup — установка и настройка балансировщика нагрузки.

Фаза HAPROXY_SETUP в пайплайне деплоя IVA Mail:
  1. Установка пакета haproxy (идемпотентно — пропускает если уже установлен)
  2. Рендер конфига из Jinja2-шаблона (haproxy.cfg.j2)
  3. Запись конфига в /etc/haproxy/haproxy.cfg
  4. Валидация конфига через haproxy -c -f
  5. Включение и перезапуск сервиса
  6. Верификация: systemctl is-active + проверка портов

Поддерживаемые ОС:
  - Debian/Ubuntu — apt-get install -y haproxy
  - RHEL/CentOS  — yum/dnf install -y haproxy
"""

import logging
import os
from pathlib import Path
from typing import List

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..infrastructure.ssh_manager import SSHManager, SSHCommandResult
from ..models.schemas import ClusterInput, ServerConfig

logger = logging.getLogger(__name__)

# Путь к директории с шаблонами относительно этого файла
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Порты, которые должен слушать HAProxy после старта
_EXPECTED_PORTS = [80, 443, 143, 993, 110, 995, 25, 587, 1936]


class HAProxySetup:
    """
    Устанавливает и настраивает HAProxy на всех haproxy_servers кластера.

    Использует тот же паттерн, что PostgreSQLSetup: принимает SSHManager,
    возвращает List[str] логов для записи в DeploymentLog.
    """

    def __init__(self, ssh: SSHManager) -> None:
        self._ssh = ssh
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    async def setup(self, cluster: ClusterInput) -> List[str]:
        """
        Полный цикл настройки HAProxy для всех haproxy_servers.

        Если haproxy_servers пуст — возвращает пустой список
        (вызывающий код в оркестраторе уже проверяет этот случай).
        """
        logs: List[str] = []
        for server in cluster.haproxy_servers:
            node_logs = await self._setup_node(server, cluster)
            logs.extend(node_logs)
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Node-level workflow
    # ─────────────────────────────────────────────────────────────────

    async def _setup_node(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []
        tag = f"{server.hostname} ({server.ip})"
        logs.append(f"→ HAProxy setup на {tag}")

        steps = [
            ("Установка пакета",         self._install_haproxy),
            ("Рендер конфигурации",      self._render_and_write_config),
            ("Валидация конфигурации",   self._validate_config),
            ("Запуск сервиса",           self._enable_and_restart),
            ("Верификация",              self._verify),
        ]

        for step_name, step_fn in steps:
            logger.info(f"[HAProxy] [{server.ip}] {step_name}...")
            step_logs = await step_fn(server, cluster)
            for line in step_logs:
                logs.append(f"  [{step_name}] {line}")

        logs.append(f"✓ HAProxy настроен на {tag}")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Steps
    # ─────────────────────────────────────────────────────────────────

    async def _install_haproxy(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []

        # Проверяем наличие установленного пакета
        check = await self._ssh.execute_command(
            server, "which haproxy", use_sudo=False
        )
        if check.success and check.stdout.strip():
            # Узнаём версию для лога
            ver = await self._ssh.execute_command(
                server, "haproxy -v 2>&1 | head -1", use_sudo=False
            )
            version_str = ver.stdout.strip() if ver.stdout.strip() else "неизвестная версия"
            logs.append(f"haproxy уже установлен: {version_str}")
            return logs

        # Определяем пакетный менеджер
        pkg_manager = await self._detect_pkg_manager(server)
        logs.append(f"Пакетный менеджер: {pkg_manager}")

        if pkg_manager == "apt":
            # Ждём освобождения dpkg lock перед установкой
            await self._ssh.wait_dpkg_lock(server)
            cmds = [
                "apt-get update -qq",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y haproxy",
            ]
        else:
            # RHEL/CentOS/Fedora
            cmds = [f"{pkg_manager} install -y haproxy"]

        for cmd in cmds:
            result = await self._run(server, cmd)
            if not result.success:
                raise RuntimeError(
                    f"[HAProxy] [{server.ip}] Ошибка установки пакета: {result.stderr}"
                )

        # Верифицируем установку
        ver = await self._ssh.execute_command(
            server, "haproxy -v 2>&1 | head -1", use_sudo=False
        )
        logs.append(f"haproxy установлен: {ver.stdout.strip()}")
        return logs

    async def _render_and_write_config(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []

        # Формируем список frontend-серверов для шаблона
        frontend_servers = cluster.frontends or []
        if not frontend_servers:
            # Fallback: используем backends как frontend-узлы
            frontend_servers = cluster.backends
            logs.append("⚠ frontends не указаны — используем backends в качестве upstream")

        # Рендерим Jinja2-шаблон
        template = self._jinja_env.get_template("haproxy.cfg.j2")
        rendered = template.render(
            frontend_servers=frontend_servers,
            stats_password="admin",
            maxconn=50000,
        )

        # Записываем конфиг через sudo (пользователь user без прав на /etc)
        # Используем механизм: echo content | sudo tee /etc/haproxy/haproxy.cfg
        # Безопаснее через временный файл в /tmp
        tmp_path = "/tmp/_haproxy_cfg_upload"

        # Записываем во временный файл через SFTP (без sudo)
        await self._write_remote_tmp(server, rendered, tmp_path)

        # Перемещаем с sudo
        mv_result = await self._run(
            server,
            f"cp '{tmp_path}' /etc/haproxy/haproxy.cfg && "
            f"chmod 644 /etc/haproxy/haproxy.cfg && "
            f"chown root:root /etc/haproxy/haproxy.cfg && "
            f"rm -f '{tmp_path}'"
        )
        if not mv_result.success:
            raise RuntimeError(
                f"[HAProxy] [{server.ip}] Не удалось записать конфиг: {mv_result.stderr}"
            )

        logs.append(f"Конфиг записан в /etc/haproxy/haproxy.cfg "
                    f"({len(frontend_servers)} upstream-серверов, "
                    f"{len(rendered)} символов)")
        return logs

    async def _validate_config(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []

        result = await self._ssh.execute_command(
            server, "haproxy -c -f /etc/haproxy/haproxy.cfg 2>&1"
        )

        if result.success:
            # haproxy -c выводит результат в stdout или stderr в зависимости от версии
            output = (result.stdout or result.stderr).strip()
            logs.append(f"Конфиг валиден: {output}")
        else:
            output = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"[HAProxy] [{server.ip}] Конфиг невалиден:\n{output}"
            )

        return logs

    async def _enable_and_restart(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []

        # Включаем автозапуск
        enable_result = await self._run(server, "systemctl enable haproxy")
        if not enable_result.success:
            logs.append(f"⚠ systemctl enable haproxy: {enable_result.stderr}")

        # Перезапускаем (или запускаем если не был запущен)
        restart_result = await self._run(server, "systemctl restart haproxy")
        if not restart_result.success:
            raise RuntimeError(
                f"[HAProxy] [{server.ip}] Не удалось перезапустить haproxy: {restart_result.stderr}"
            )

        logs.append("haproxy включён в автозапуск и перезапущен")
        return logs

    async def _verify(self, server: ServerConfig, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []

        # Проверяем статус сервиса
        status_result = await self._ssh.execute_command(
            server, "systemctl is-active haproxy", use_sudo=False
        )
        service_state = status_result.stdout.strip()
        if service_state != "active":
            raise RuntimeError(
                f"[HAProxy] [{server.ip}] Сервис не активен: '{service_state}'"
            )
        logs.append(f"systemctl is-active haproxy: {service_state} ✓")

        # Проверяем ключевые порты (80, 443, 1936)
        ports_to_check = [80, 443, 1936]
        ports_str = "|".join(str(p) for p in ports_to_check)
        ss_result = await self._ssh.execute_command(
            server,
            f"ss -tlnp 2>/dev/null | grep -E ':{ports_str}\\b' | awk '{{print $4}}'",
            use_sudo=False,
        )
        listening = ss_result.stdout.strip()
        if listening:
            ports_found = ", ".join(listening.split("\n"))
            logs.append(f"Слушает порты: {ports_found} ✓")
        else:
            logs.append("⚠ Порты 80/443/1936 не обнаружены через ss — сервис возможно стартует")

        # Проверяем stats endpoint доступен изнутри
        curl_result = await self._ssh.execute_command(
            server, "curl -s -o /dev/null -w '%{http_code}' http://localhost:1936/stats",
            use_sudo=False,
        )
        http_code = curl_result.stdout.strip()
        if http_code in ("200", "401"):
            # 401 — тоже OK (требует auth), значит haproxy отвечает
            logs.append(f"Stats endpoint :1936/stats → HTTP {http_code} ✓")
        else:
            logs.append(f"⚠ Stats endpoint :1936/stats → HTTP {http_code or 'нет ответа'}")

        return logs

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    async def _detect_pkg_manager(self, server: ServerConfig) -> str:
        """Определяет пакетный менеджер: apt, dnf или yum."""
        for pm in ("apt-get", "dnf", "yum"):
            result = await self._ssh.execute_command(
                server, f"which {pm} 2>/dev/null", use_sudo=False
            )
            if result.success and result.stdout.strip():
                return "apt" if pm == "apt-get" else pm
        return "apt"  # fallback

    async def _write_remote_tmp(self, server: ServerConfig, content: str, remote_path: str) -> None:
        """
        Записывает строковый контент в удалённый временный файл через SFTP.
        Использует существующий paramiko-клиент из SSHManager._get_or_create_connection.
        """
        import asyncio

        def _write_via_sftp():
            client = self._ssh._get_or_create_connection(server)
            sftp = client.open_sftp()
            with sftp.file(remote_path, "w") as f:
                f.write(content)
            sftp.close()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._ssh._executor, _write_via_sftp)
        logger.debug(f"[HAProxy] [{server.ip}] Временный файл записан: {remote_path}")

    async def _run(self, server: ServerConfig, cmd: str) -> SSHCommandResult:
        """Выполняет команду с sudo и логирует предупреждение при неуспехе."""
        result = await self._ssh.execute_command(server, cmd)
        if not result.success:
            logger.warning(
                f"[HAProxy] [{server.ip}] exit={result.exit_code}: {cmd[:80]}"
            )
        return result
