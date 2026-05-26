"""
PHASE 3.5A: CMD Protocol Client

Текстовый TCP-клиент для CMD-протокола IVA Mail (порт 106).
Реализует:
  - Подключение + чтение приветственного баннера
  - Аутентификацию (LOGIN метод)
  - Отправку команд и получение ответов
  - Лицензионные команды: LicenseRequest, LicenseInstall
  - QUIT для корректного завершения сессии

Протокол:
  Server: 200 hostname IVA Mail version <challenge@domain>
  Client: AUTH LOGIN
  Server: Username:
  Client: <username>
  Server: Password:
  Client: <password>
  Server: 200 authenticated as user@domain

Лицензионные команды:
  Client: LicenseRequest "-----" {"LicensedAccounts":50000,...}
  Server: 200 OK
  Server: "-----BEGIN IVAMAIL LICENSE REQUEST-----\n<base64>\n-----END IVAMAIL LICENSE REQUEST-----\n"
  (контент — вторая строка, quoted, \n как \\n)

  Client: LicenseInstall "-----BEGIN IVAMAIL LICENSE-----\n...\n-----END IVAMAIL LICENSE-----\n"
  Server: 200 OK

Коды ответов:
  200 — OK
  4xx — Временная ошибка
  5xx — Постоянная ошибка
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..models.license_models import LicenseRequestParams

logger = logging.getLogger(__name__)

CMD_DEFAULT_PORT = 106
CMD_TIMEOUT = 30.0
CMD_BUFFER_SIZE = 65536


class CMDError(Exception):
    """Ошибка CMD-протокола."""
    pass


class CMDAuthError(CMDError):
    """Ошибка аутентификации."""
    pass


class CMDConnectionError(CMDError):
    """Ошибка подключения."""
    pass


class CMDLicenseError(CMDError):
    """Ошибка лицензионной операции."""
    pass


@dataclass
class CMDResponse:
    """Ответ CMD-сервера."""
    code: int
    lines: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.code < 300

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def __repr__(self) -> str:
        return f"<CMDResponse code={self.code} ok={self.ok} lines={len(self.lines)}>"


def _build_license_request_json(params: LicenseRequestParams) -> str:
    """
    Сериализует LicenseRequestParams в JSON для команды LicenseRequest.
    Делегирует маппинг методу to_cmd_dict() — единая точка маппинга полей.
    """
    return json.dumps(params.to_cmd_dict(), ensure_ascii=False)


class CMDClient:
    """
    Асинхронный клиент CMD-протокола IVA Mail.

    Использует asyncio streams. Поддерживает LOGIN-аутентификацию.

    Использование:
        async with CMDClient(host, port) as cmd:
            await cmd.authenticate(user, password)
            req_content = await cmd.license_request(params)
            await cmd.license_install(license_txt_content)
    """

    def __init__(
        self,
        host: str,
        port: int = CMD_DEFAULT_PORT,
        timeout: float = CMD_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._banner: Optional[str] = None
        self._authenticated = False

    # ─────────────────────────────────────────────────────────────────
    # Context manager
    # ─────────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "CMDClient":
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ─────────────────────────────────────────────────────────────────
    # Connection
    # ─────────────────────────────────────────────────────────────────

    async def connect(self) -> str:
        """Открывает TCP-соединение и читает приветственный баннер."""
        logger.debug(f"[CMD] Подключение к {self.host}:{self.port}")
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
        except (OSError, asyncio.TimeoutError) as e:
            raise CMDConnectionError(
                f"Не удалось подключиться к {self.host}:{self.port}: {e}"
            ) from e

        banner_response = await self._read_response()
        if not banner_response.ok:
            raise CMDConnectionError(
                f"Неожиданный баннер от {self.host}: {banner_response.raw}"
            )
        self._banner = banner_response.text
        logger.info(f"[CMD] Подключён к {self.host}:{self.port} | {self._banner[:80]}")
        return self._banner

    async def close(self) -> None:
        """Корректно завершает сессию (QUIT) и закрывает соединение."""
        if self._writer and not self._writer.is_closing():
            try:
                await self._send_line("QUIT")
                await asyncio.wait_for(self._read_response(), timeout=5.0)
            except Exception:
                pass
            finally:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:
                    pass
        self._authenticated = False
        logger.debug(f"[CMD] Соединение закрыто: {self.host}:{self.port}")

    # ─────────────────────────────────────────────────────────────────
    # Authentication
    # ─────────────────────────────────────────────────────────────────

    async def authenticate(self, username: str, password: str) -> None:
        """
        Аутентификация через AUTH LOGIN.

        Сессия:
          Client: AUTH LOGIN
          Server: Username:
          Client: <username>
          Server: Password:
          Client: <password>
          Server: 200 authenticated as user@domain
        """
        if not self._reader or not self._writer:
            raise CMDConnectionError("Нет активного соединения")

        logger.debug(f"[CMD] Аутентификация: {username}")

        await self._send_line("AUTH LOGIN")
        r1 = await self._read_response()
        if "username" not in r1.text.lower() and not r1.ok:
            raise CMDAuthError(f"Неожиданный ответ на AUTH LOGIN: {r1.raw}")

        await self._send_line(username)
        r2 = await self._read_response()
        if "password" not in r2.text.lower() and not r2.ok:
            raise CMDAuthError(f"Неожиданный ответ на username: {r2.raw}")

        await self._send_line(password)
        r3 = await self._read_response()
        if not r3.ok:
            raise CMDAuthError(
                f"Аутентификация провалена для {username}@{self.host}: {r3.raw}"
            )

        self._authenticated = True
        logger.info(f"[CMD] Аутентификация успешна: {r3.text}")

    # ─────────────────────────────────────────────────────────────────
    # License commands
    # ─────────────────────────────────────────────────────────────────

    async def license_request(self, params: LicenseRequestParams) -> str:
        """
        Отправляет LicenseRequest и возвращает содержимое файла-запроса.

        Реальный протокол сервера (две строки):
          Client: LicenseRequest "-----" {JSON}
          Server: 200 OK
          Server: "-----BEGIN IVAMAIL LICENSE REQUEST-----\n<base64>\n-----END IVAMAIL LICENSE REQUEST-----\n"

        Контент передаётся второй строкой — quoted, \n как литерал \\n.
        """
        json_payload = _build_license_request_json(params)
        command = f'LicenseRequest "-----" {json_payload}'

        logger.info(
            f"[CMD] LicenseRequest: accounts={params.licensed_accounts} "
            f"backends={params.cluster_backends} frontends={params.cluster_frontends}"
        )
        response = await self.execute(command)

        if not response.ok:
            raise CMDLicenseError(
                f"LicenseRequest провалился (код {response.code}): {response.text}"
            )

        # Читаем вторую строку — содержимое файла-запроса
        raw_content = await asyncio.wait_for(
            self._reader.readline(), timeout=self.timeout
        )
        content_line = raw_content.decode("utf-8", errors="replace").rstrip("\r\n")

        # Сервер оборачивает содержимое в двойные кавычки с \n как \\n
        if content_line.startswith('"') and content_line.endswith('"'):
            content = content_line[1:-1]
            content = content.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
        else:
            content = content_line

        logger.info(
            f"[CMD] LicenseRequest успешен, "
            f"размер ответа: {len(content)} символов"
        )
        return content

    async def license_install(self, license_content: str) -> None:
        """
        Устанавливает лицензию через LicenseInstall.

        Принимает полное содержимое .txt файла лицензии:
          -----BEGIN IVAMAIL LICENSE-----
          <base64 data>
          -----END IVAMAIL LICENSE-----

        Протокол:
          Client: LicenseInstall "<escaped_content>"
          Server: 200 OK
        """
        # Экранируем содержимое для передачи как строковый параметр CMD
        # Переносы строк → \n, кавычки → \"
        escaped = license_content.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
        command = f'LicenseInstall "{escaped}"'

        logger.info(f"[CMD] LicenseInstall: размер файла {len(license_content)} символов")
        response = await self.execute(command)

        if not response.ok:
            raise CMDLicenseError(
                f"LicenseInstall провалился (код {response.code}): {response.text}"
            )

        logger.info(f"[CMD] LicenseInstall успешен: {response.text}")

    # ─────────────────────────────────────────────────────────────────
    # General commands
    # ─────────────────────────────────────────────────────────────────

    async def execute(self, command: str) -> CMDResponse:
        """
        Выполняет произвольную команду и возвращает ответ.

        Args:
            command: строка команды, например:
                     'PING'
                     'ModuleReadConfig "CMD"'
                     'LicenseRequest "-----" {...}'
        """
        if not self._reader or not self._writer:
            raise CMDConnectionError("Нет активного соединения")
        if not self._authenticated:
            raise CMDError("Требуется аутентификация перед выполнением команд")

        logger.debug(f"[CMD] → {command[:120]}")
        await self._send_line(command)
        response = await self._read_response()
        logger.debug(f"[CMD] ← {response}")
        return response

    async def ping(self) -> bool:
        """Проверяет доступность сервера (PING)."""
        try:
            response = await self.execute("PING")
            return response.ok
        except Exception:
            return False

    async def _read_body(self, timeout: float = 5.0) -> str:
        """Читает многострочное тело ответа после «200 OK».

        Сервер IVA Mail посылает тело (JSON-объект или JSON-массив) в виде
        pretty-printed текста, не предваряя строки кодом ответа.
        Чтение заканчивается, когда встречается закрывающая «}» или «]»
        на отдельной строке, либо по таймауту.
        """
        lines = []
        try:
            while True:
                raw = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=timeout,
                )
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                lines.append(line)
                if line.rstrip() in ("}", "]"):
                    break
        except asyncio.TimeoutError:
            logger.debug("[CMD] _read_body: таймаут при чтении тела ответа")
        return "\n".join(lines)

    async def execute_with_body(self, command: str) -> CMDResponse:
        """Выполняет команду и читает многострочное JSON-тело после «200 OK»."""
        resp = await self.execute(command)
        if resp.ok:
            body = await self._read_body(timeout=min(self.timeout, 8.0))
            if body:
                return CMDResponse(code=resp.code, lines=[body], raw=resp.raw + "\n" + body)
        return resp

    async def system_info(self) -> CMDResponse:
        """SystemInfo — информация о состоянии сервера IVA Mail.

        Протокол: сервер шлёт «200 OK\r\n», затем многострочный JSON-объект
        без кода ответа (pretty-printed). _read_response останавливается
        после «200 OK», поэтому тело читается явно через _read_body().

        Возвращает JSON-объект: Server version, System UpTime, Cluster Status, Total Accounts, etc.
        """
        resp = await self.execute("SystemInfo")
        if not resp.ok:
            return resp
        body = await self._read_body(timeout=min(self.timeout, 5.0))
        if body:
            return CMDResponse(
                code=resp.code,
                lines=[body],
                raw=resp.raw + "\n" + body,
            )
        return resp

    async def get_module_setting(self, module: str, key: str) -> CMDResponse:
        return await self.execute(f'ModuleGetSetting "{module}" "{key}"')

    async def set_module_setting(self, module: str, key: str, value: str) -> CMDResponse:
        return await self.execute(f'ModuleSetSetting "{module}" "{key}" {value}')

    async def read_module_config(self, module: str) -> CMDResponse:
        return await self.execute_with_body(f'ModuleReadConfig "{module}"')

    # ─────────────────────────────────────────────────────────────────
    # Config management — новые методы для Config Store
    # ─────────────────────────────────────────────────────────────────

    async def modules_list(self) -> CMDResponse:
        """ModulesList — список инициализированных модулей."""
        return await self.execute_with_body("ModulesList")

    async def schema_dump(
        self,
        scope: Optional[str] = None,
        include_rights: bool = True,
    ) -> CMDResponse:
        """
        SchemaDump "type" (false) — описания полей настроек по схеме.

        scope=None или "" → выдаёт список известных схем.
        scope="SomeType"  → выдаёт описание схемы SomeType.
        include_rights=False → описания объектов без прав доступа.
        """
        type_arg = scope if scope is not None else ""
        if include_rights:
            cmd = f'SchemaDump "{type_arg}"'
        else:
            cmd = f'SchemaDump "{type_arg}" false'
        return await self.execute_with_body(cmd)

    async def domains_list(self) -> CMDResponse:
        """DOMAINSLIST — список зарегистрированных доменов."""
        return await self.execute_with_body("DOMAINSLIST")

    async def domain_read_config(self, domain: str) -> CMDResponse:
        """DOMAINREADCONFIG — выдаёт объект настроек домена."""
        return await self.execute_with_body(f'DOMAINREADCONFIG "{domain}"')

    async def domain_update_config(
        self,
        domain: str,
        keys_to_delete: list,
        key_val_pairs: list,
    ) -> CMDResponse:
        """DOMAINUPDATECONFIG — обновляет настройки домена."""
        kd_str  = json.dumps(keys_to_delete,  ensure_ascii=False, separators=(',', ':'))
        kvp_str = json.dumps(key_val_pairs,   ensure_ascii=False, separators=(',', ':'))
        return await self.execute(f'DOMAINUPDATECONFIG "{domain}" {kd_str} {kvp_str}')

    async def objects_list(
        self,
        domain: str,
        obj_type: Optional[str] = None,
    ) -> CMDResponse:
        """OBJECTSLIST — список объектов в домене (с опциональной фильтрацией по типу)."""
        if obj_type:
            return await self.execute_with_body(f'OBJECTSLIST "{domain}" "{obj_type}"')
        return await self.execute_with_body(f'OBJECTSLIST "{domain}"')

    async def object_read_config(self, domain: str, uid: str) -> CMDResponse:
        """OBJECTREADCONFIG — выдаёт объект настроек учётной записи / группы / ресурса."""
        return await self.execute_with_body(f'OBJECTREADCONFIG "{domain}" "{uid}"')

    async def object_update_config(
        self,
        domain: str,
        uid: str,
        keys_to_delete: list,
        key_val_pairs: list,
    ) -> CMDResponse:
        """OBJECTUPDATECONFIG — обновляет настройки объекта."""
        kd_str  = json.dumps(keys_to_delete,  ensure_ascii=False, separators=(',', ':'))
        kvp_str = json.dumps(key_val_pairs,   ensure_ascii=False, separators=(',', ':'))
        return await self.execute(f'OBJECTUPDATECONFIG "{domain}" "{uid}" {kd_str} {kvp_str}')

    async def directory_find(
        self,
        base_dn: str,
        scope: str,
        filter: str = "",
        fields: Optional[list] = None,
        sorting: str = "",
        cookie: str = "",
        limit: int = 100,
    ) -> CMDResponse:
        """
        DirectoryFind "base_dn" "scope" "filter" ["fields"] "sorting" "cookie" limit

        base_dn  — базовый DN поиска
        scope    — "base" | "one" | "sub"
        filter   — LDAP-фильтр, например "(cn=Ivan*)", может быть пустым
        fields   — список атрибутов для выборки, None → все ("*")
        sorting  — "[+-]attrname" или пустая строка
        cookie   — пагинация (пустая строка для первой страницы)
        limit    — максимум записей (1..500)
        """
        fields_json = json.dumps(fields if fields is not None else ["*"],
                                 ensure_ascii=False, separators=(",", ":"))
        cmd = (
            f'DirectoryFind "{base_dn}" "{scope}" "{filter}" '
            f'{fields_json} "{sorting}" "{cookie}" {limit}'
        )
        return await self.execute(cmd)

    # Alias: doc-style name → snake_case implementation
    async def module_read_config(
        self,
        module: str,
        with_descriptions: Optional[bool] = None,
    ) -> CMDResponse:
        """
        ModuleReadConfig "module_name" (true|false)

        with_descriptions=True  → словарь содержит описания полей
        with_descriptions=False → словарь без значений по умолчанию
        with_descriptions=None  → стандартный ответ
        """
        if with_descriptions is True:
            return await self.execute_with_body(f'ModuleReadConfig "{module}" true')
        if with_descriptions is False:
            return await self.execute_with_body(f'ModuleReadConfig "{module}" false')
        return await self.read_module_config(module)

    async def module_update_config(
        self,
        module: str,
        keys_to_delete: list,
        key_val_pairs: list,
    ) -> CMDResponse:
        """
        Обновляет настройки модуля.

        Пример для кластерной конфигурации:
          module = "Cluster"
          keys_to_delete = []
          key_val_pairs = ["BackendList", ["/I [10.3.6.200]", "/I [10.3.6.201]"],
                           "OwnAddress", "/I [10.3.6.200]", "Password", "admin"]

        Wire format:
          ModuleUpdateConfig "Cluster" [] ["BackendList",["/I [...]"],...]
        """
        import json
        kd_str  = json.dumps(keys_to_delete,  ensure_ascii=False, separators=(',', ':'))
        kvp_str = json.dumps(key_val_pairs,   ensure_ascii=False, separators=(',', ':'))
        command = f'ModuleUpdateConfig "{module}" {kd_str} {kvp_str}'
        return await self.execute(command)

    # ─────────────────────────────────────────────────────────────────
    # Общие команды сервера
    # ─────────────────────────────────────────────────────────────────

    async def shutdown(self) -> CMDResponse:
        """SHUTDOWN — инициирует остановку сервера."""
        return await self.execute("SHUTDOWN")

    async def help(self, search: Optional[str] = None) -> CMDResponse:
        """HELP ["search"] — список команд с опциональной фильтрацией.

        Протокол: «200 OK\r\n» затем многострочный JSON-массив строк вида
        «"CommandName args"». Читается через _read_body().
        """
        if search:
            resp = await self.execute(f'HELP "{search}"')
        else:
            resp = await self.execute("HELP")

        if resp.ok:
            body = await self._read_body(timeout=min(self.timeout, 5.0))
            if body:
                return CMDResponse(code=resp.code, lines=[body], raw=resp.raw + "\n" + body)
        return resp

    async def mail_modules_list(self) -> CMDResponse:
        """MailModulesList — список инициализированных модулей обработки почты."""
        return await self.execute("MailModulesList")

    async def mail_queue_list(self, module_name: Optional[str] = None) -> CMDResponse:
        """MailQueueList ("module_name") — информация о почтовой очереди модуля."""
        if module_name:
            return await self.execute(f'MailQueueList "{module_name}"')
        return await self.execute("MailQueueList")

    async def mail_batch_list(self, module_name: str, active: bool = True) -> CMDResponse:
        """MailBatchList "module_name" (true) — блоки доставки очереди."""
        flag = "true" if active else ""
        cmd = f'MailBatchList "{module_name}"' + (f" {flag}" if flag else "")
        return await self.execute(cmd)

    async def mail_queue_info(self, queue_id: str) -> CMDResponse:
        """MailQueueInfo queueID — информация о сообщении."""
        return await self.execute(f'MailQueueInfo {queue_id}')

    async def mail_queue_fail(
        self, queue_id: str, error_msg: Optional[str] = None
    ) -> CMDResponse:
        """MailQueueFail queueID ("error message") — удаляет сообщение из очереди."""
        if error_msg:
            return await self.execute(f'MailQueueFail {queue_id} "{error_msg}"')
        return await self.execute(f'MailQueueFail {queue_id}')

    async def module_set_log_level(self, module: str, level: int) -> CMDResponse:
        """MODULESetLogLevel "module_name" value — детализация журналирования."""
        return await self.execute(f'MODULESetLogLevel "{module}" {level}')

    async def module_del_setting(self, module: str, key: str) -> CMDResponse:
        """ModuleDelSetting "module_name" "key" — удаляет настройку модуля."""
        return await self.execute(f'ModuleDelSetting "{module}" "{key}"')

    async def threads_list(self, filter: Optional[str] = None) -> CMDResponse:
        """ThreadsList ("filter") — список активных нитей исполнения."""
        if filter:
            return await self.execute(f'ThreadsList "{filter}"')
        return await self.execute("ThreadsList")

    async def connections_list(
        self,
        module: Optional[str] = None,
        filter: Optional[str] = None,
    ) -> CMDResponse:
        """ConnectionsList ("module" ("filter")) — список активных сетевых соединений."""
        if module and filter:
            return await self.execute(f'ConnectionsList "{module}" "{filter}"')
        if module:
            return await self.execute(f'ConnectionsList "{module}"')
        return await self.execute("ConnectionsList")

    async def logs_list(self, log_type: Optional[str] = None) -> CMDResponse:
        """LogsList ("log_type") — список лог-файлов. Типы: Mailbox, Auth, Settings."""
        if log_type:
            return await self.execute(f'LogsList "{log_type}"')
        return await self.execute("LogsList")

    async def logs_remove(self, log_type: str, log_names: list) -> CMDResponse:
        """LogsRemove "log_type" ["name1",...] — удаляет лог-файлы."""
        names_json = json.dumps(log_names, ensure_ascii=False, separators=(",", ":"))
        return await self.execute(f'LogsRemove "{log_type}" {names_json}')

    async def maintenance(
        self, target: str, subtask: Optional[str] = None
    ) -> CMDResponse:
        """
        maintenance "*"|"domain.dom"|"user@domain.dom" ("SubtaskName")

        Запускает задачу Maintenance. Подзадачи: CleanTrash, CleanSpam,
        CleanMail, RestructureMail, ArchiveMail, CleanCalendars,
        RestructureCalendar, CleanDomain, RepairServer, RepairDomain, RepairAccount.
        """
        if subtask:
            return await self.execute(f'maintenance "{target}" "{subtask}"')
        return await self.execute(f'maintenance "{target}"')

    async def feature_flag_set(self, flags: list, enabled: bool) -> CMDResponse:
        """FeatureFlagSet ["flagName",...] enabled — включает/выключает фичи."""
        flags_json = json.dumps(flags, ensure_ascii=False, separators=(",", ":"))
        return await self.execute(f'FeatureFlagSet {flags_json} {"true" if enabled else "false"}')

    # ─────────────────────────────────────────────────────────────────
    # Работа с доменами
    # ─────────────────────────────────────────────────────────────────

    async def domain_create(self, name: str, cluster: bool = False) -> CMDResponse:
        """DOMAINCREATE "string" (null) — создание домена. cluster=True → кластерный домен."""
        if cluster:
            return await self.execute(f'DOMAINCREATE "{name}" null')
        return await self.execute(f'DOMAINCREATE "{name}"')

    async def domain_remove(self, name_or_uid: str) -> CMDResponse:
        """DOMAINREMOVE "string"|domUID — удаление домена."""
        return await self.execute(f'DOMAINREMOVE "{name_or_uid}"')

    async def domain_rename(self, name_or_uid: str, new_name: str) -> CMDResponse:
        """DOMAINRENAME "string1"|domUID "string2" — переименование домена."""
        return await self.execute(f'DOMAINRENAME "{name_or_uid}" "{new_name}"')

    async def domain_get_id(self, name: str) -> CMDResponse:
        """DOMAINGETID "string" — UID домена по имени."""
        return await self.execute(f'DOMAINGETID "{name}"')

    async def domain_list_names(self, name_or_uid: str) -> CMDResponse:
        """DOMAINLISTNAMES "string"|domUID — все имена домена."""
        return await self.execute(f'DOMAINLISTNAMES "{name_or_uid}"')

    async def domain_add_name(
        self, name_or_uid: str, new_name: str, flag: Optional[int] = None
    ) -> CMDResponse:
        """DOMAINADDNAME "string1"|domUID "string2" (flag) — добавляет псевдоним домена."""
        if flag is not None:
            return await self.execute(f'DOMAINADDNAME "{name_or_uid}" "{new_name}" {flag}')
        return await self.execute(f'DOMAINADDNAME "{name_or_uid}" "{new_name}"')

    async def domain_del_name(self, name: str) -> CMDResponse:
        """DOMAINDELNAME "string1" — удаляет псевдоним домена."""
        return await self.execute(f'DOMAINDELNAME "{name}"')

    async def domain_get_setting(self, name_or_uid: str, key: str) -> CMDResponse:
        """DOMAINGETSETTING "string"|domUID "key" — объект настроек домена."""
        return await self.execute_with_body(f'DOMAINGETSETTING "{name_or_uid}" "{key}"')

    async def domain_set_setting(
        self, name_or_uid: str, key: str, value: Any
    ) -> CMDResponse:
        """DOMAINSETSETTING "string"|domUID "key" object — устанавливает настройку домена."""
        val_str = json.dumps(value, ensure_ascii=False)
        return await self.execute(f'DOMAINSETSETTING "{name_or_uid}" "{key}" {val_str}')

    async def domain_del_setting(self, name_or_uid: str, key: str) -> CMDResponse:
        """DOMAINDELSETTING "string"|domUID "key" — удаляет настройку домена."""
        return await self.execute(f'DOMAINDELSETTING "{name_or_uid}" "{key}"')

    async def domain_get_multiset(
        self, name_or_uid: str, keys: list
    ) -> CMDResponse:
        """DOMAINGETMULTISET "string"|domUID [keysToRequest] — несколько настроек сразу."""
        keys_json = json.dumps(keys, ensure_ascii=False, separators=(",", ":"))
        return await self.execute_with_body(f'DOMAINGETMULTISET "{name_or_uid}" {keys_json}')

    async def domains_defaults(
        self,
        with_descriptions: Optional[bool] = None,
        cluster: bool = False,
    ) -> CMDResponse:
        """DOMAINSDEFAULTS (true|false) (null) — настройки доменов по умолчанию."""
        parts = ["DOMAINSDEFAULTS"]
        if with_descriptions is True:
            parts.append("true")
        elif with_descriptions is False:
            parts.append("false")
        if cluster:
            parts.append("null")
        return await self.execute_with_body(" ".join(parts))

    async def domains_update_defs(
        self,
        keys_to_delete: list,
        key_val_pairs: list,
        cluster: bool = False,
    ) -> CMDResponse:
        """DOMAINSUPDATEDEFS keysToDelete keyValPairs (null) — обновляет дефолты доменов."""
        kd = json.dumps(keys_to_delete, ensure_ascii=False, separators=(",", ":"))
        kvp = json.dumps(key_val_pairs, ensure_ascii=False, separators=(",", ":"))
        cmd = f'DOMAINSUPDATEDEFS {kd} {kvp}'
        if cluster:
            cmd += " null"
        return await self.execute(cmd)

    async def domain_static_password(
        self, name_or_uid: str, password: Optional[str] = None
    ) -> CMDResponse:
        """DomainStaticPassword "domname"|domUID ("password") — пароль для статического кластера."""
        if password is not None:
            return await self.execute(f'DomainStaticPassword "{name_or_uid}" "{password}"')
        return await self.execute(f'DomainStaticPassword "{name_or_uid}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа с объектами в доменах
    # ─────────────────────────────────────────────────────────────────

    async def account_create(
        self,
        domain: str,
        accname: str,
        settings: Optional[dict] = None,
    ) -> CMDResponse:
        """ACCOUNTCREATE "domname" "accname" (settingsDict) — создание учётной записи."""
        if settings:
            s = json.dumps(settings, ensure_ascii=False)
            return await self.execute(f'ACCOUNTCREATE "{domain}" "{accname}" {s}')
        return await self.execute(f'ACCOUNTCREATE "{domain}" "{accname}"')

    async def resource_create(
        self, domain: str, resname: str, owner: str
    ) -> CMDResponse:
        """RESOURCECREATE "domname" "resname" "owner" — создание ресурсного аккаунта."""
        return await self.execute(f'RESOURCECREATE "{domain}" "{resname}" "{owner}"')

    async def group_create(
        self,
        domain: str,
        grpname: str,
        settings: Optional[dict] = None,
    ) -> CMDResponse:
        """GROUPCREATE "domname" "grpname" (settingsDict) — создание группы."""
        if settings:
            s = json.dumps(settings, ensure_ascii=False)
            return await self.execute(f'GROUPCREATE "{domain}" "{grpname}" {s}')
        return await self.execute(f'GROUPCREATE "{domain}" "{grpname}"')

    async def forwarder_create(
        self, domain: str, fwdname: str, destination: str
    ) -> CMDResponse:
        """FORWARDERCREATE "domname" "fwdname" "destination" — создание переадресатора."""
        return await self.execute(f'FORWARDERCREATE "{domain}" "{fwdname}" "{destination}"')

    async def object_remove(self, domain: str, name_or_uid: str) -> CMDResponse:
        """OBJECTREMOVE "domname" "objname"|objUID — удаление объекта."""
        return await self.execute(f'OBJECTREMOVE "{domain}" "{name_or_uid}"')

    async def object_rename(
        self, domain: str, name_or_uid: str, new_name: str
    ) -> CMDResponse:
        """OBJECTRENAME "domname" "objName"|objUID "newName" — переименование объекта."""
        return await self.execute(f'OBJECTRENAME "{domain}" "{name_or_uid}" "{new_name}"')

    async def object_get_id(self, domain: str, name: str) -> CMDResponse:
        """OBJECTGETID "domname" "objname" — UID объекта по имени."""
        return await self.execute(f'OBJECTGETID "{domain}" "{name}"')

    async def object_list_names(self, domain: str, name_or_uid: str) -> CMDResponse:
        """OBJECTLISTNAMES "domname" "objname"|objUID — все имена объекта."""
        return await self.execute(f'OBJECTLISTNAMES "{domain}" "{name_or_uid}"')

    async def object_get_setting(
        self, domain: str, obj: str, key: str, merge_defaults: bool = False
    ) -> CMDResponse:
        """
        OBJECTGETSETTING "domname" "objname" "key" (true)

        merge_defaults=True → объединяет значения со всеми уровнями по умолчанию.
        """
        if merge_defaults:
            return await self.execute_with_body(f'OBJECTGETSETTING "{domain}" "{obj}" "{key}" true')
        return await self.execute_with_body(f'OBJECTGETSETTING "{domain}" "{obj}" "{key}"')

    async def object_set_setting(
        self, domain: str, obj: str, key: str, value: Any
    ) -> CMDResponse:
        """OBJECTSETSETTING "domname" "objname" "key" object — устанавливает настройку объекта."""
        val_str = json.dumps(value, ensure_ascii=False)
        return await self.execute(f'OBJECTSETSETTING "{domain}" "{obj}" "{key}" {val_str}')

    async def object_del_setting(self, domain: str, obj: str, key: str) -> CMDResponse:
        """OBJECTDELSETTING "domname" "objname" "key" — удаляет настройку объекта."""
        return await self.execute(f'OBJECTDELSETTING "{domain}" "{obj}" "{key}"')

    async def object_get_multiset(
        self, domain: str, obj: str, keys: list
    ) -> CMDResponse:
        """ObjectGetMultiset "string" "objname" [keysToRequest] — несколько настроек сразу."""
        keys_json = json.dumps(keys, ensure_ascii=False, separators=(",", ":"))
        return await self.execute_with_body(f'ObjectGetMultiset "{domain}" "{obj}" {keys_json}')

    async def object_has_in_list(
        self, domain: str, obj: str, key: str, value: Any
    ) -> CMDResponse:
        """OBJECTHASINLIST — проверяет наличие значения в списке настройки объекта."""
        val_str = json.dumps(value, ensure_ascii=False)
        return await self.execute(f'OBJECTHASINLIST "{domain}" "{obj}" "{key}" {val_str}')

    async def object_add_to_list(
        self, domain: str, obj: str, key: str, value: Any
    ) -> CMDResponse:
        """OBJECTADDTOLIST — добавляет значение в список настройки объекта."""
        val_str = json.dumps(value, ensure_ascii=False)
        return await self.execute(f'OBJECTADDTOLIST "{domain}" "{obj}" "{key}" {val_str}')

    async def object_del_in_list(
        self, domain: str, obj: str, key: str, value: Any
    ) -> CMDResponse:
        """OBJECTDELINLIST — удаляет значение из списка настройки объекта."""
        val_str = json.dumps(value, ensure_ascii=False)
        return await self.execute(f'OBJECTDELINLIST "{domain}" "{obj}" "{key}" {val_str}')

    async def object_export(self, domain: str, obj: str, prefix: str) -> CMDResponse:
        """ObjectExport "domname" "objname" "prefix" — экспорт объекта в архив."""
        return await self.execute(f'ObjectExport "{domain}" "{obj}" "{prefix}"')

    async def object_import(self, domain: str, path: str) -> CMDResponse:
        """ObjectImport "domname" "path" — импорт объекта из архива."""
        return await self.execute(f'ObjectImport "{domain}" "{path}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа с аккаунтами
    # ─────────────────────────────────────────────────────────────────

    async def account_set_password(
        self,
        domain: str,
        account: str,
        password: str,
        tag: Optional[str] = None,
    ) -> CMDResponse:
        """ACCOUNTSETPASSWORD "domname" "accname" "string" ("tag") — задаёт пароль аккаунта."""
        if tag is not None:
            return await self.execute(
                f'ACCOUNTSETPASSWORD "{domain}" "{account}" "{password}" "{tag}"'
            )
        return await self.execute(
            f'ACCOUNTSETPASSWORD "{domain}" "{account}" "{password}"'
        )

    async def account_del_password(
        self, domain: str, account: str, tag: Optional[str] = None
    ) -> CMDResponse:
        """ACCOUNTDELPASSWORD "domname" "accname" ("tag") — удаляет пароль аккаунта."""
        if tag is not None:
            return await self.execute(
                f'ACCOUNTDELPASSWORD "{domain}" "{account}" "{tag}"'
            )
        return await self.execute(f'ACCOUNTDELPASSWORD "{domain}" "{account}"')

    async def account_verify_password(
        self, domain: str, account: str, password: str, tag: Optional[str] = None
    ) -> CMDResponse:
        """ACCOUNTVERIFYPWD — проверяет пароль аккаунта."""
        if tag is not None:
            return await self.execute(
                f'ACCOUNTVERIFYPWD "{domain}" "{account}" "{password}" "{tag}"'
            )
        return await self.execute(
            f'ACCOUNTVERIFYPWD "{domain}" "{account}" "{password}"'
        )

    async def account_get_mail_storage_size(
        self, domain: str, account: str, recalc: bool = False
    ) -> CMDResponse:
        """AccountGetMailStorageSize "domname" "accname" (true) — размер почтового хранилища."""
        if recalc:
            return await self.execute(
                f'AccountGetMailStorageSize "{domain}" "{account}" true'
            )
        return await self.execute(f'AccountGetMailStorageSize "{domain}" "{account}"')

    async def account_close_sessions(self, domain: str, account: str) -> CMDResponse:
        """AccountСloseSessions "domname" "accname" — закрывает все сессии аккаунта."""
        return await self.execute(f'AccountСloseSessions "{domain}" "{account}"')

    async def accounts_defaults(
        self,
        domain: str,
        profile_name: Optional[str] = None,
        with_descriptions: Optional[bool] = None,
    ) -> CMDResponse:
        """
        AccountsDefaults "domname"|null ("profileName") (true|false)

        domain="" → уровень сервера; domain=None → уровень кластера.
        """
        dom = "null" if domain is None else f'"{domain}"'
        parts = [f"AccountsDefaults {dom}"]
        if profile_name:
            parts.append(f'"{profile_name}"')
        if with_descriptions is True:
            parts.append("true")
        elif with_descriptions is False:
            parts.append("false")
        return await self.execute(" ".join(parts))

    async def accounts_update_defs(
        self,
        domain: str,
        keys_to_delete: list,
        key_val_pairs: list,
        profile_name: Optional[str] = None,
    ) -> CMDResponse:
        """AccountsUpdateDefs — обновляет дефолты аккаунтов."""
        dom = "null" if domain is None else f'"{domain}"'
        kd = json.dumps(keys_to_delete, ensure_ascii=False, separators=(",", ":"))
        kvp = json.dumps(key_val_pairs, ensure_ascii=False, separators=(",", ":"))
        cmd = f'AccountsUpdateDefs {dom} {kd} {kvp}'
        if profile_name:
            cmd += f' "{profile_name}"'
        return await self.execute(cmd)

    # ─────────────────────────────────────────────────────────────────
    # Работа с ресурсными аккаунтами
    # ─────────────────────────────────────────────────────────────────

    async def resource_get_owner(self, domain: str, resname: str) -> CMDResponse:
        """ResourceGetOwner "dom.name" "resname" — имя владельца ресурсного аккаунта."""
        return await self.execute(f'ResourceGetOwner "{domain}" "{resname}"')

    async def resource_set_owner(
        self, domain: str, resname: str, owner: str
    ) -> CMDResponse:
        """ResourceSetOwner "dom.name" "resname" "owner" — задаёт владельца ресурса."""
        return await self.execute(f'ResourceSetOwner "{domain}" "{resname}" "{owner}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа с группами
    # ─────────────────────────────────────────────────────────────────

    async def group_list_members(
        self,
        domain: str,
        group: str,
        emails: bool = False,
        filter: Optional[str] = None,
    ) -> CMDResponse:
        """GroupListMembers "domname" "grpname" (true ("?filter?")) — список членов группы."""
        if emails and filter:
            return await self.execute(
                f'GroupListMembers "{domain}" "{group}" true "?{filter}?"'
            )
        if emails:
            return await self.execute(f'GroupListMembers "{domain}" "{group}" true')
        return await self.execute(f'GroupListMembers "{domain}" "{group}"')

    async def group_add_account(
        self, domain: str, group: str, address: str, by_email: bool = False
    ) -> CMDResponse:
        """GroupAddAccount "domname" "grpname" "address" (true) — добавляет адрес в группу."""
        if by_email:
            return await self.execute(
                f'GroupAddAccount "{domain}" "{group}" "{address}" true'
            )
        return await self.execute(f'GroupAddAccount "{domain}" "{group}" "{address}"')

    async def group_del_account(
        self, domain: str, group: str, address: str, by_route: bool = False
    ) -> CMDResponse:
        """GroupDelAccount "domname" "grpname" "address" (true) — удаляет адрес из группы."""
        if by_route:
            return await self.execute(
                f'GroupDelAccount "{domain}" "{group}" "{address}" true'
            )
        return await self.execute(f'GroupDelAccount "{domain}" "{group}" "{address}"')

    async def group_has_account(
        self, domain: str, group: str, address: str, by_email: bool = False
    ) -> CMDResponse:
        """GroupHasAccount "domname" "grpname" "address" (true) — проверяет членство."""
        if by_email:
            return await self.execute(
                f'GroupHasAccount "{domain}" "{group}" "{address}" true'
            )
        return await self.execute(f'GroupHasAccount "{domain}" "{group}" "{address}"')

    async def group_get_owner(self, domain: str, group: str) -> CMDResponse:
        """GroupGetOwner "dom.name" "grpname" — имя владельца группы."""
        return await self.execute(f'GroupGetOwner "{domain}" "{group}"')

    async def group_set_owner(self, domain: str, group: str, address: str) -> CMDResponse:
        """GroupSetOwner "dom.name" "grpname" "address" — задаёт владельца группы."""
        return await self.execute(f'GroupSetOwner "{domain}" "{group}" "{address}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа с мейлбоксами
    # ─────────────────────────────────────────────────────────────────

    async def mailboxes_list(
        self,
        domain: str,
        account: str,
        cls: Optional[str] = None,
        mode: int = 0,
        parent_uid: Optional[int] = None,
    ) -> CMDResponse:
        """
        MAILBOXESLIST "domname" "accname" ("class") (optMode (optParent))

        cls   — "mail" | "calendar" | "contacts" | "notes" | "tasks"
        mode  — 0 UTF-8 (default) | 1 UTF-7 | 2 subdir names
        """
        parts = [f'MAILBOXESLIST "{domain}" "{account}"']
        if cls:
            parts.append(f'"{cls}"')
        if parent_uid is not None:
            parts.append(str(mode))
            parts.append(str(parent_uid))
        elif cls:
            # cls was added; no mode needed
            pass
        return await self.execute(" ".join(parts))

    async def mailbox_create(
        self,
        domain: str,
        account: str,
        full_name: str,
        cls: Optional[str] = None,
    ) -> CMDResponse:
        """MAILBOXCREATE "domname" "accname" "fullName" ("class") — создание мейлбокса."""
        if cls:
            return await self.execute(
                f'MAILBOXCREATE "{domain}" "{account}" "{full_name}" "{cls}"'
            )
        return await self.execute(f'MAILBOXCREATE "{domain}" "{account}" "{full_name}"')

    async def mailbox_rename(
        self, domain: str, account: str, old_name: str, new_name: str
    ) -> CMDResponse:
        """MAILBOXRENAME "domname" "accname" "oldFullName" "newFullName" — переименование."""
        return await self.execute(
            f'MAILBOXRENAME "{domain}" "{account}" "{old_name}" "{new_name}"'
        )

    async def mailbox_remove(
        self, domain: str, account: str, full_name: str
    ) -> CMDResponse:
        """MAILBOXREMOVE "domname" "accname" "fullName" — удаление мейлбокса."""
        return await self.execute(
            f'MAILBOXREMOVE "{domain}" "{account}" "{full_name}"'
        )

    async def mailbox_get_acl(
        self, domain: str, account: str, mailbox: str
    ) -> CMDResponse:
        """MailboxGetACL "domname" "accname" "fullName" — ACL мейлбокса."""
        return await self.execute(f'MailboxGetACL "{domain}" "{account}" "{mailbox}"')

    async def mailbox_update_acl(
        self,
        domain: str,
        account: str,
        mailbox: str,
        identifier: str,
        modifier: Optional[str] = None,
    ) -> CMDResponse:
        """
        MailboxUpdateACL "domname" "accname" "fullName" "identifier" ("modifier")

        modifier: "+rwi" добавить права, "-w" убрать, "" удалить identifier, "*" все права.
        """
        if modifier is not None:
            return await self.execute(
                f'MailboxUpdateACL "{domain}" "{account}" "{mailbox}" "{identifier}" "{modifier}"'
            )
        return await self.execute(
            f'MailboxUpdateACL "{domain}" "{account}" "{mailbox}" "{identifier}"'
        )

    async def mailbox_get_my_rights(
        self, domain: str, account: str, mailbox: str
    ) -> CMDResponse:
        """MailboxGetMyRights "domname" "accname" "fullName" — права аккаунта на мейлбокс."""
        return await self.execute(f'MailboxGetMyRights "{domain}" "{account}" "{mailbox}"')

    async def mbox_get_info(
        self, domain: str, account: str, mailbox: str, key: Optional[str] = None
    ) -> CMDResponse:
        """MboxGetInfo "domname" "accname" "mailboxname" ("settingskey") — метаданные папки."""
        if key:
            return await self.execute(f'MboxGetInfo "{domain}" "{account}" "{mailbox}" "{key}"')
        return await self.execute(f'MboxGetInfo "{domain}" "{account}" "{mailbox}"')

    async def mbox_set_info(
        self, domain: str, account: str, mailbox: str, key: str, value: Any = None
    ) -> CMDResponse:
        """MboxSetInfo "domname" "accname" "mailboxname" "key" (object) — устанавливает метаданные."""
        if value is not None:
            val_str = json.dumps(value, ensure_ascii=False)
            return await self.execute(
                f'MboxSetInfo "{domain}" "{account}" "{mailbox}" "{key}" {val_str}'
            )
        return await self.execute(f'MboxSetInfo "{domain}" "{account}" "{mailbox}" "{key}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа с сессиями
    # ─────────────────────────────────────────────────────────────────

    async def session_list(
        self,
        domain: Optional[str] = None,
        account: Optional[str] = None,
    ) -> CMDResponse:
        """SESSIONLIST ("domname" ("accname")) — список активных сессий."""
        if domain and account:
            return await self.execute(f'SESSIONLIST "{domain}" "{account}"')
        if domain:
            return await self.execute(f'SESSIONLIST "{domain}"')
        return await self.execute("SESSIONLIST")

    async def session_create(self, domain: str, account: str) -> CMDResponse:
        """SESSIONCREATE "domname" "accname" — создаёт новую сессию, возвращает токен."""
        return await self.execute(f'SESSIONCREATE "{domain}" "{account}"')

    async def session_close(self, token: str) -> CMDResponse:
        """SESSIONCLOSE "string" — закрывает сессию по токену."""
        return await self.execute(f'SESSIONCLOSE "{token}"')

    async def session_get_info(self, token: str) -> CMDResponse:
        """SESSIONGETINFO "string" — данные сессии по токену."""
        return await self.execute(f'SESSIONGETINFO "{token}"')

    async def session_update_info(self, token: str, data: dict) -> CMDResponse:
        """SESSIONUPDATEINFO "string" object — обновляет данные сессии."""
        data_str = json.dumps(data, ensure_ascii=False)
        return await self.execute(f'SESSIONUPDATEINFO "{token}" {data_str}')

    async def session_send_event(self, token: str, event: dict) -> CMDResponse:
        """SESSIONSENDEVENT "string" object — отправляет событие в сессию."""
        event_str = json.dumps(event, ensure_ascii=False)
        return await self.execute(f'SESSIONSENDEVENT "{token}" {event_str}')

    # ─────────────────────────────────────────────────────────────────
    # Работа с административными правами
    # ─────────────────────────────────────────────────────────────────

    async def rights_list(self, nested: bool = False) -> CMDResponse:
        """RightsList (true) — список административных прав текущего администратора."""
        if nested:
            return await self.execute("RightsList true")
        return await self.execute("RightsList")

    async def admins_list(self, domain: Optional[str] = None) -> CMDResponse:
        """
        AdminsList "dom.name"|null — список администраторов.
        domain=""   → уровень сервера
        domain=None → уровень кластера
        """
        dom = "null" if domain is None else f'"{domain}"'
        return await self.execute(f'AdminsList {dom}')

    async def admin_create(
        self, domain: Optional[str], user: str, rights: Optional[list] = None
    ) -> CMDResponse:
        """AdminCreate "dom.name"|null "user@domain" ([rights]) — назначает администратора."""
        dom = "null" if domain is None else f'"{domain}"'
        if rights is not None:
            r = json.dumps(rights, ensure_ascii=False, separators=(",", ":"))
            return await self.execute(f'AdminCreate {dom} "{user}" {r}')
        return await self.execute(f'AdminCreate {dom} "{user}"')

    async def admin_remove(self, domain: Optional[str], user: str) -> CMDResponse:
        """AdminRemove "dom.name"|null "user@domain" — удаляет администратора."""
        dom = "null" if domain is None else f'"{domain}"'
        return await self.execute(f'AdminRemove {dom} "{user}"')

    async def admin_info(self, domain: Optional[str], user: str) -> CMDResponse:
        """AdminInfo "dom.name"|null "user@domain" — права и роли администратора."""
        dom = "null" if domain is None else f'"{domain}"'
        return await self.execute(f'AdminInfo {dom} "{user}"')

    async def admin_update(
        self,
        domain: Optional[str],
        user: str,
        rights_del: list,
        rights_add: list,
    ) -> CMDResponse:
        """AdminUpdate — изменяет права администратора (удалить/добавить)."""
        dom = "null" if domain is None else f'"{domain}"'
        rd = json.dumps(rights_del, ensure_ascii=False, separators=(",", ":"))
        ra = json.dumps(rights_add, ensure_ascii=False, separators=(",", ":"))
        return await self.execute(f'AdminUpdate {dom} "{user}" {rd} {ra}')

    async def admin_set(
        self,
        domain: Optional[str],
        user: str,
        rights: list,
        force: bool = False,
    ) -> CMDResponse:
        """AdminSet — заменяет все права администратора. force=True — автоназначение."""
        dom = "null" if domain is None else f'"{domain}"'
        r = json.dumps(rights, ensure_ascii=False, separators=(",", ":"))
        cmd = f'AdminSet {dom} "{user}" {r}'
        if force:
            cmd += " true"
        return await self.execute(cmd)

    async def roles_list(self, domain: Optional[str] = None) -> CMDResponse:
        """RolesList "dom.name"|null — список административных ролей."""
        dom = "null" if domain is None else f'"{domain}"'
        return await self.execute(f'RolesList {dom}')

    async def role_create(
        self, domain: Optional[str], role: str, rights: Optional[list] = None
    ) -> CMDResponse:
        """RoleCreate "dom.name"|null "role" ([rights]) — создаёт роль."""
        dom = "null" if domain is None else f'"{domain}"'
        if rights is not None:
            r = json.dumps(rights, ensure_ascii=False, separators=(",", ":"))
            return await self.execute(f'RoleCreate {dom} "{role}" {r}')
        return await self.execute(f'RoleCreate {dom} "{role}"')

    async def role_remove(self, domain: Optional[str], role: str) -> CMDResponse:
        """RoleRemove "dom.name"|null "role" — удаляет роль."""
        dom = "null" if domain is None else f'"{domain}"'
        return await self.execute(f'RoleRemove {dom} "{role}"')

    async def role_get(self, domain: Optional[str], role: str) -> CMDResponse:
        """RoleGet "dom.name"|null "role" — права роли."""
        dom = "null" if domain is None else f'"{domain}"'
        return await self.execute(f'RoleGet {dom} "{role}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа со справочником
    # ─────────────────────────────────────────────────────────────────

    async def directory_add(self, dn: str, attributes: dict) -> CMDResponse:
        """DirectoryAdd "dn" {attributes} — добавляет запись в справочник."""
        attr_str = json.dumps(attributes, ensure_ascii=False)
        return await self.execute(f'DirectoryAdd "{dn}" {attr_str}')

    async def directory_update(self, dn: str, changed_attributes: dict) -> CMDResponse:
        """DirectoryUpdate "dn" {changed_attributes} — обновляет запись справочника."""
        attr_str = json.dumps(changed_attributes, ensure_ascii=False)
        return await self.execute(f'DirectoryUpdate "{dn}" {attr_str}')

    async def directory_move(self, dn: str, new_dn: str) -> CMDResponse:
        """DirectoryMove "dn" "new_dn" — перемещает запись справочника."""
        return await self.execute(f'DirectoryMove "{dn}" "{new_dn}"')

    async def directory_remove(self, dn: str) -> CMDResponse:
        """DirectoryRemove "dn" — удаляет запись справочника."""
        return await self.execute(f'DirectoryRemove "{dn}"')

    async def directory_cleanup(
        self, dn: str = "", min_tombstone_time: Optional[str] = None
    ) -> CMDResponse:
        """DirectoryCleanup "dn" ["min_tombstone_time"] — удаляет записи об удалённых объектах."""
        if min_tombstone_time:
            return await self.execute(
                f'DirectoryCleanup "{dn}" ["{min_tombstone_time}"]'
            )
        return await self.execute(f'DirectoryCleanup "{dn}"')

    async def domains_to_directory(self, domains: Optional[list] = None) -> CMDResponse:
        """DomainsToDirectory — синхронизирует справочник для доменов."""
        if domains:
            d = json.dumps(domains, ensure_ascii=False, separators=(",", ":"))
            return await self.execute(f'DomainsToDirectory {d}')
        return await self.execute('DomainsToDirectory []')

    # ─────────────────────────────────────────────────────────────────
    # Белые и чёрные списки сетевых адресов
    # ─────────────────────────────────────────────────────────────────

    async def get_whitelisted(self, ip_block: str = "0.0.0.0/0") -> CMDResponse:
        """GetWhitelisted "IP address block" — белый список адресов (CIDR)."""
        return await self.execute(f'GetWhitelisted "{ip_block}"')

    async def get_blacklisted(self, ip_block: str = "0.0.0.0/0") -> CMDResponse:
        """GetBlacklisted "IP address block" — чёрный список адресов (CIDR)."""
        return await self.execute(f'GetBlacklisted "{ip_block}"')

    async def set_whitelisted(self, addresses: list) -> CMDResponse:
        """SetWhitelisted ["IP",...] — добавляет адреса в белый список."""
        addr_json = json.dumps(addresses, ensure_ascii=False, separators=(",", ":"))
        return await self.execute(f'SetWhitelisted {addr_json}')

    async def set_blacklisted(self, addresses: list) -> CMDResponse:
        """SetBlacklisted ["IP",...] — добавляет адреса в чёрный список."""
        addr_json = json.dumps(addresses, ensure_ascii=False, separators=(",", ":"))
        return await self.execute(f'SetBlacklisted {addr_json}')

    async def del_whitelisted(self, addresses: list) -> CMDResponse:
        """DelWhitelisted ["IP",...] — удаляет адреса из белого списка."""
        addr_json = json.dumps(addresses, ensure_ascii=False, separators=(",", ":"))
        return await self.execute(f'DelWhitelisted {addr_json}')

    async def del_blacklisted(self, addresses: list) -> CMDResponse:
        """DelBlacklisted ["IP",...] — удаляет адреса из чёрного списка."""
        addr_json = json.dumps(addresses, ensure_ascii=False, separators=(",", ":"))
        return await self.execute(f'DelBlacklisted {addr_json}')

    # ─────────────────────────────────────────────────────────────────
    # Работа со списками адресов
    # ─────────────────────────────────────────────────────────────────

    async def address_list_get(self, name: Optional[str] = None) -> CMDResponse:
        """
        AddressListGet ("name") — список адресов.
        Специальные имена: $BLOCKED, $UNBLOCKABLE, $DEBUG.
        """
        if name:
            return await self.execute(f'AddressListGet "{name}"')
        return await self.execute("AddressListGet")

    async def address_list_update(
        self,
        name: str,
        content: Optional[str] = None,
    ) -> CMDResponse:
        """
        AddressListUpdate "name" ("content"|null)

        content=str  → заменяет список
        content=None → удаляет список
        """
        if content is not None:
            escaped = content.replace('"', '\\"').replace('\n', '\\n')
            return await self.execute(f'AddressListUpdate "{name}" "{escaped}"')
        return await self.execute(f'AddressListUpdate "{name}" null')

    async def address_list_check(self, ip: str, list_text: str) -> CMDResponse:
        """
        AddressListCheck "IP address" "address list as text"

        Результат: 1=включён, 0=нет, -1=исключён, -2/-3=ошибка.
        """
        escaped = list_text.replace('"', '\\"').replace('\n', '\\n')
        return await self.execute(f'AddressListCheck "{ip}" "{escaped}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа с правилами обработки почты
    # ─────────────────────────────────────────────────────────────────

    async def rules_list(
        self, domain: Optional[str] = None, obj: Optional[str] = None
    ) -> CMDResponse:
        """
        RulesList "dom.name"|null "objname"|null

        domain=None, obj=None → правила уровня сервера.
        """
        dom = "null" if domain is None else f'"{domain}"'
        ob = "null" if obj is None else f'"{obj}"'
        return await self.execute(f'RulesList {dom} {ob}')

    async def rule_get(
        self, domain: Optional[str], obj: Optional[str], rule_name: str
    ) -> CMDResponse:
        """RuleGet — конфигурация правила."""
        dom = "null" if domain is None else f'"{domain}"'
        ob = "null" if obj is None else f'"{obj}"'
        return await self.execute(f'RuleGet {dom} {ob} "{rule_name}"')

    async def rule_add(
        self, domain: Optional[str], obj: Optional[str], rule_name: str, params: dict
    ) -> CMDResponse:
        """RuleAdd — создаёт правило обработки почты."""
        dom = "null" if domain is None else f'"{domain}"'
        ob = "null" if obj is None else f'"{obj}"'
        p = json.dumps(params, ensure_ascii=False)
        return await self.execute(f'RuleAdd {dom} {ob} "{rule_name}" {p}')

    async def rule_set(
        self, domain: Optional[str], obj: Optional[str], rule_name: str, params: dict
    ) -> CMDResponse:
        """RuleSet — изменяет правило обработки почты."""
        dom = "null" if domain is None else f'"{domain}"'
        ob = "null" if obj is None else f'"{obj}"'
        p = json.dumps(params, ensure_ascii=False)
        return await self.execute(f'RuleSet {dom} {ob} "{rule_name}" {p}')

    async def rule_del(
        self, domain: Optional[str], obj: Optional[str], rule_name: str
    ) -> CMDResponse:
        """RuleDel — удаляет правило обработки почты."""
        dom = "null" if domain is None else f'"{domain}"'
        ob = "null" if obj is None else f'"{obj}"'
        return await self.execute(f'RuleDel {dom} {ob} "{rule_name}"')

    # ─────────────────────────────────────────────────────────────────
    # Работа с профилями SMTP
    # ─────────────────────────────────────────────────────────────────

    async def smtpi_profiles_list(
        self, name: Optional[str] = None, with_descriptions: Optional[bool] = None
    ) -> CMDResponse:
        """smtpiProfilesList ("name") (true|false) — профили SMTP-Input."""
        parts = ["smtpiProfilesList"]
        if name:
            parts.append(f'"{name}"')
        if with_descriptions is True:
            parts.append("true")
        elif with_descriptions is False:
            parts.append("false")
        return await self.execute(" ".join(parts))

    async def smtpo_profiles_list(
        self, name: Optional[str] = None, with_descriptions: Optional[bool] = None
    ) -> CMDResponse:
        """SMTPOProfilesList ("name") (true|false) — профили SMTP-Output."""
        parts = ["SMTPOProfilesList"]
        if name:
            parts.append(f'"{name}"')
        if with_descriptions is True:
            parts.append("true")
        elif with_descriptions is False:
            parts.append("false")
        return await self.execute(" ".join(parts))

    # ─────────────────────────────────────────────────────────────────
    # Работа с планировщиком задач
    # ─────────────────────────────────────────────────────────────────

    async def task_schedule(
        self,
        target: str,
        action: str,
        time: str,
        period: Optional[str] = None,
        arg: Any = None,
    ) -> CMDResponse:
        """
        TaskSchedule Param1 "Param2" "Param3" ("Param4" ("Param5"))

        target  — "*" | "domain.dom" | "user@domain.dom" | "=objUID@=domUID"
        action  — имя действия (напр. "Maintenance", "WriteToLog")
        time    — время в формате объекта времени IVA Mail (напр. '/T 2025-09-01T01:00:00.000Z')
        period  — периодичность (напр. '/D 24h')
        arg     — произвольный аргумент для действия
        """
        parts = [f'TaskSchedule "{target}" "{action}" "{time}"']
        if period:
            parts.append(f'"{period}"')
        if arg is not None:
            parts.append(json.dumps(arg, ensure_ascii=False))
        return await self.execute(" ".join(parts))

    async def task_cancel(self, task_uid: str) -> CMDResponse:
        """TaskCancel TaskUID — удаляет задание из расписания."""
        return await self.execute(f'TaskCancel {task_uid}')

    async def tasks_list(
        self,
        cluster: bool = False,
        from_time: Optional[str] = None,
        max_count: Optional[int] = None,
    ) -> CMDResponse:
        """TasksList Param1 ("Param2" ("Param3")) — список запланированных заданий."""
        flag = "true" if cluster else "false"
        parts = [f'TasksList {flag}']
        if from_time:
            parts.append(f'"{from_time}"')
        if max_count is not None:
            parts.append(str(max_count))
        return await self.execute(" ".join(parts))

    # ─────────────────────────────────────────────────────────────────
    # Работа с полнотекстовым поиском
    # ─────────────────────────────────────────────────────────────────

    async def full_text_search(
        self,
        domain: str,
        account: str,
        mailbox: str,
        query: str,
        fields: Optional[list] = None,
    ) -> CMDResponse:
        """
        FullTextSearch "domname" "accname" "mailbox.name" "query" (["fields"])

        Поля поиска: Body, Attachment, Subject, From, To, Cc, Bcc.
        """
        if fields:
            f = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
            return await self.execute(
                f'FullTextSearch "{domain}" "{account}" "{mailbox}" "{query}" {f}'
            )
        return await self.execute(
            f'FullTextSearch "{domain}" "{account}" "{mailbox}" "{query}"'
        )

    async def ft_index_sync(self, target: str = "*") -> CMDResponse:
        """
        FTIndexSync [ "*" | "domain.dom" | "user@domain.dom" ]
        Синхронизирует полнотекстовые индексы.
        """
        return await self.execute(f'FTIndexSync "{target}"')

    # ─────────────────────────────────────────────────────────────────
    # Прочие утилиты
    # ─────────────────────────────────────────────────────────────────

    async def http_request(self, uri: str, method: str, body: str = "") -> CMDResponse:
        """HttpRequest "uri" "method" "body" — HTTP-запрос от имени сервера."""
        escaped_body = body.replace('"', '\\"').replace('\n', '\\n')
        return await self.execute(f'HttpRequest "{uri}" "{method}" "{escaped_body}"')

    async def timezone_list(self) -> CMDResponse:
        """TimeZoneList — список временных зон, известных серверу."""
        return await self.execute("TimeZoneList")

    # ─────────────────────────────────────────────────────────────────
    # Low-level I/O
    # ─────────────────────────────────────────────────────────────────

    async def _send_line(self, line: str) -> None:
        """Отправляет строку + CRLF."""
        data = (line + "\r\n").encode("utf-8")
        self._writer.write(data)
        await asyncio.wait_for(self._writer.drain(), timeout=self.timeout)

    async def _read_response(self) -> CMDResponse:
        """
        Читает ответ CMD-сервера.

        Форматы:
          "NNN text"       — однострочный финальный ответ
          "NNN-text"       — строка многострочного ответа (продолжение)
          "Username:" / "Password:" / "+" — промпты аутентификации
        """
        lines_acc = []
        code = 0
        raw_parts = []

        while True:
            try:
                raw_line = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError as e:
                raise CMDError(f"Таймаут чтения ответа от {self.host}") from e

            if not raw_line:
                raise CMDError(f"Соединение закрыто сервером {self.host}")

            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            raw_parts.append(line)

            # Промпты (Username:, Password:, +)
            if re.match(r'^(Username:|Password:|\+)', line, re.IGNORECASE):
                return CMDResponse(code=200, lines=[line], raw=line)

            # Стандартный ответ: "NNN text" или "NNN-text" (продолжение)
            m = re.match(r'^(\d{3})([- ]?)(.*)', line)
            if m:
                code = int(m.group(1))
                separator = m.group(2)
                text = m.group(3)
                lines_acc.append(text)
                # "-" = продолжение; " " или пусто = последняя строка
                if separator != '-':
                    break
            else:
                # Данные без кода (тело многострочного ответа)
                lines_acc.append(line)

        return CMDResponse(
            code=code,
            lines=lines_acc,
            raw="\n".join(raw_parts),
        )


# ─────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────

async def create_cmd_session(
    host: str,
    username: str,
    password: str,
    port: int = CMD_DEFAULT_PORT,
    timeout: float = CMD_TIMEOUT,
) -> CMDClient:
    """
    Открывает аутентифицированную CMD-сессию.
    Вызывающий код отвечает за вызов close().
    """
    client = CMDClient(host, port, timeout)
    await client.connect()
    await client.authenticate(username, password)
    return client
