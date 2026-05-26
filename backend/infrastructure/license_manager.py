"""
PHASE 3.5B: License Manager (CMD Protocol)

Оркестрирует полный цикл лицензирования кластера IVA Mail
через CMD-протокол (порт 106).

Реальный процесс (подтверждён командами протокола):

  1. Запустить ivamail на Backend 1 (без лицензии — ограниченный режим)
  2. CMD: LicenseRequest "-----" {JSON с параметрами лицензии}
     → Сервер возвращает содержимое файла-запроса
  3. Сохранить файл-запрос, передать администратору для отправки вендору
  4. Ждать: администратор загружает .txt файл лицензии через
     POST /api/deployment/{id}/upload-license
  5. CMD: LicenseInstall "<содержимое .txt файла>"
     → Лицензия применяется немедленно, без перезапуска
  6. CMD: LicenseInstall тем же содержимым на Backend 2..N
     → Каждый узел получает лицензию напрямую

Ключевое отличие от SFTP-подхода:
  - Нет загрузки файлов на сервер
  - Нет systemctl restart
  - Всё через один TCP-сокет на порт 106
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..infrastructure.ssh_manager import SSHManager
from ..infrastructure.cmd_client import CMDClient, CMDConnectionError, create_cmd_session
from ..models.schemas import ClusterInput, ServerConfig
from ..models.license_models import LicenseRequestParams
from ..core.config import settings

logger = logging.getLogger(__name__)

IVAMAIL_SERVICE = "ivamail"
CMD_USER = "admin"
CMD_PASS = "admin"


@dataclass
class LicenseRequestInfo:
    """Результат LicenseRequest — данные для передачи вендору."""
    deployment_id: str
    backend1_ip: str
    backend1_hostname: str
    license_params: LicenseRequestParams
    request_file_content: str     # Ответ сервера на LicenseRequest
    request_file_path: str        # Локальный путь сохранённого файла запроса
    created_at: str


class LicenseUploadWaiter:
    """
    Механизм ожидания загрузки .txt файла лицензии от администратора.

    Singleton per deployment_id.
    Оркестратор блокируется на asyncio.Event.wait().
    API эндпоинт вызывает notify() при получении файла.
    """

    _instances: dict[str, "LicenseUploadWaiter"] = {}

    def __init__(self, deployment_id: str, timeout: int):
        self.deployment_id = deployment_id
        self.timeout = timeout
        self._event = asyncio.Event()
        self._license_file_path: Optional[str] = None

    @classmethod
    def get_or_create(cls, deployment_id: str, timeout: int) -> "LicenseUploadWaiter":
        if deployment_id not in cls._instances:
            cls._instances[deployment_id] = cls(deployment_id, timeout)
        return cls._instances[deployment_id]

    @classmethod
    def get(cls, deployment_id: str) -> Optional["LicenseUploadWaiter"]:
        return cls._instances.get(deployment_id)

    @classmethod
    def cleanup(cls, deployment_id: str) -> None:
        cls._instances.pop(deployment_id, None)

    def notify(self, license_file_path: str) -> None:
        """Вызывается из API когда администратор загрузил .txt файл."""
        self._license_file_path = license_file_path
        self._event.set()
        logger.info(
            f"[License] Файл лицензии получен для {self.deployment_id}: "
            f"{license_file_path}"
        )

    async def wait_for_license(self) -> Optional[str]:
        """
        Ждёт уведомления. Возвращает путь к файлу или None при таймауте.
        """
        logger.info(
            f"[License] Ожидание лицензии для {self.deployment_id} "
            f"(таймаут: {self.timeout}с)"
        )
        try:
            await asyncio.wait_for(self._event.wait(), timeout=float(self.timeout))
            return self._license_file_path
        except asyncio.TimeoutError:
            logger.warning(
                f"[License] Таймаут ожидания лицензии для {self.deployment_id}"
            )
            return None


class LicenseManager:
    """
    Оркестратор лицензирования кластера IVA Mail через CMD-протокол.

    Все операции с лицензией выполняются через TCP порт 106 —
    никакого SFTP, никаких рестартов сервисов.
    """

    def __init__(self, ssh: SSHManager):
        self._ssh = ssh

    # ─────────────────────────────────────────────────────────────────
    # Step 1: Запуск Backend 1 и генерация запроса лицензии
    # ─────────────────────────────────────────────────────────────────

    async def prepare_license_request(
        self,
        deployment_id: str,
        cluster: ClusterInput,
        license_params: LicenseRequestParams,
    ) -> LicenseRequestInfo:
        """
        Подключается по CMD к Backend 1,
        выполняет LicenseRequest и сохраняет файл-запрос.
        """
        primary = cluster.backends[0]
        logger.info(f"[License] Запрос лицензии с {primary.hostname} ({primary.ip})")

        await self._verify_node_ready(primary)

        # ── CMD: LicenseRequest ──
        async with CMDClient(primary.ip, settings.ivamail_port) as cmd:
            await cmd.authenticate(CMD_USER, CMD_PASS)
            request_content = await cmd.license_request(license_params)

        # ── Сохраняем файл-запрос локально ──
        request_file_path = await self._save_request_file(
            deployment_id, request_content
        )

        logger.info(
            f"[License] Файл запроса сохранён: {request_file_path} "
            f"({len(request_content)} символов)"
        )

        return LicenseRequestInfo(
            deployment_id=deployment_id,
            backend1_ip=primary.ip,
            backend1_hostname=primary.hostname,
            license_params=license_params,
            request_file_content=request_content,
            request_file_path=request_file_path,
            created_at=datetime.utcnow().isoformat(),
        )

    # ─────────────────────────────────────────────────────────────────
    # Step 2: Ожидание .txt файла от администратора
    # ─────────────────────────────────────────────────────────────────

    async def wait_for_license_file(self, deployment_id: str) -> str:
        """
        Блокирует выполнение до получения .txt файла через API.
        Использует файловый polling вместо asyncio.Event —
        корректно работает при uvicorn --workers > 1 (нет общей памяти между воркерами).
        Возвращает локальный путь к файлу.
        """
        license_file = (
            Path(settings.upload_dir) / "licenses" / f"{deployment_id}_license.txt"
        )
        timeout = float(settings.license_request_timeout_seconds)
        poll_interval = 5.0  # опрос каждые 5 секунд
        elapsed = 0.0

        logger.info(
            f"[License] Ожидание файла лицензии: {license_file} "
            f"(таймаут: {int(timeout)}с, опрос каждые {int(poll_interval)}с)"
        )

        while not license_file.exists():
            if elapsed >= timeout:
                raise TimeoutError(
                    f"Таймаут ожидания лицензии для deployment {deployment_id}. "
                    f"Загрузите файл через POST /api/deployment/{deployment_id}/upload-license"
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        license_path = str(license_file)
        logger.info(f"[License] Файл лицензии обнаружен: {license_path}")
        return license_path

    # ─────────────────────────────────────────────────────────────────
    # Step 3: Установка лицензии на Backend 1
    # ─────────────────────────────────────────────────────────────────

    async def install_on_primary(
        self,
        primary: ServerConfig,
        license_file_path: str,
    ) -> List[str]:
        """
        CMD: LicenseInstall на Backend 1.
        Лицензия применяется немедленно — без перезапуска сервиса.
        """
        logs: List[str] = []
        license_content = Path(license_file_path).read_text(encoding="utf-8")

        logger.info(f"[License] LicenseInstall на {primary.hostname} ({primary.ip})")

        async with CMDClient(primary.ip, settings.ivamail_port) as cmd:
            await cmd.authenticate(CMD_USER, CMD_PASS)
            await cmd.license_install(license_content)

        logs.append(
            f"[{primary.hostname}] Лицензия установлена через CMD:LicenseInstall ✓"
        )
        logger.info(f"[License] Лицензия установлена на {primary.hostname}")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Full orchestration entry point
    # ─────────────────────────────────────────────────────────────────

    async def run_license_phase(
        self,
        deployment_id: str,
        cluster: ClusterInput,
        license_params: LicenseRequestParams,
    ) -> List[str]:
        """Полный цикл лицензирования."""
        logs: List[str] = []
        primary = cluster.backends[0]

        # ── Шаг 1: LicenseRequest ──
        logs.append("Шаг 1/3: Генерация запроса лицензии через CMD...")
        request_info = await self.prepare_license_request(
            deployment_id, cluster, license_params
        )
        logs.append(f"Файл запроса сгенерирован: {request_info.request_file_path}")
        logs.append(f"Размер: {len(request_info.request_file_content)} символов")
        logs.append("Передайте файл запроса вендору IVA Mail для получения лицензии.")

        # ── Шаг 2: Ожидание .txt файла ──
        logs.append("Шаг 2/3: Ожидание загрузки файла лицензии (.txt) администратором...")
        logs.append(f"  → POST /api/deployment/{deployment_id}/upload-license")
        license_path = await self.wait_for_license_file(deployment_id)
        logs.append(f"Файл лицензии получен: {os.path.basename(license_path)}")

        # ── Шаг 3: LicenseInstall на Backend 1 ──
        logs.append(f"Шаг 3/3: Установка лицензии на {primary.hostname}...")
        primary_logs = await self.install_on_primary(primary, license_path)
        logs.extend(primary_logs)

        logs.append("Шаг 3/3: Лицензия установлена на Backend 1. Остальные узлы получат лицензию через кластерный протокол при перезапуске с --backend.")

        LicenseUploadWaiter.cleanup(deployment_id)
        logs.append("Фаза лицензирования завершена ✓")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    async def _wait_for_cmd_port(
        self,
        server: ServerConfig,
        max_wait: int = 60,
        interval: int = 3,
    ) -> None:
        """
        Ждёт пока CMD-порт (106) начнёт принимать соединения.
        Использует SSH: проверяем через nc/bash TCP-check без CMDClient.
        """
        for attempt in range(max_wait // interval):
            result = await self._ssh.execute_command(
                server,
                f"bash -c 'echo > /dev/tcp/127.0.0.1/{settings.ivamail_port}' 2>/dev/null && echo open || echo closed"
            )
            if "open" in result.stdout:
                logger.info(
                    f"[License] CMD-порт {settings.ivamail_port} на {server.hostname} "
                    f"доступен (попытка {attempt + 1})"
                )
                return
            await asyncio.sleep(interval)

        raise RuntimeError(
            f"[License] CMD-порт {settings.ivamail_port} на {server.hostname} "
            f"не открылся за {max_wait}с"
        )

    async def _verify_node_ready(
        self,
        server: ServerConfig,
        retries: int = 5,
        retry_interval: float = 3.0,
    ) -> None:
        """
        После открытия TCP-порта проверяет готовность CMD-модуля через PING.
        Повторяет до retries раз — CMD-модуль может инициализироваться чуть позже порта.
        """
        for attempt in range(1, retries + 1):
            try:
                async with CMDClient(server.ip, settings.ivamail_port) as cmd:
                    await cmd.authenticate(CMD_USER, CMD_PASS)
                    if await cmd.ping():
                        logger.info(
                            f"[License] CMD PING OK: {server.hostname} "
                            f"(попытка {attempt}/{retries})"
                        )
                        return
            except Exception as e:
                logger.debug(
                    f"[License] CMD PING неудача на {server.hostname} "
                    f"(попытка {attempt}/{retries}): {e}"
                )
            if attempt < retries:
                await asyncio.sleep(retry_interval)

        raise RuntimeError(
            f"[License] CMD-модуль на {server.hostname} не ответил на PING "
            f"за {retries} попыток"
        )

    async def _save_request_file(
        self,
        deployment_id: str,
        content: str,
    ) -> str:
        """Сохраняет содержимое файла-запроса лицензии локально."""
        upload_dir = Path(settings.upload_dir) / "license_requests"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / f"{deployment_id}_request.txt"
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)
