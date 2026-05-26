#!/usr/bin/env python3
"""
CMD-клиент IVA Mail (порт 106) — CLI-обёртка для Ansible.

Реализует подключение и аутентификацию по протоколу IVA Mail CMD (TCP, порт 106).
Поддерживает действия: ping, cluster-config, request, install, module-read,
module-update, domain-list, domain-read, domain-update, object-list, object-read,
object-update, config-dump, config-restore, help-discover.

Учётные данные читаются исключительно из переменных окружения:
  IVAMAIL_CMD_USER     — имя пользователя
  IVAMAIL_CMD_PASSWORD — пароль

Протокол ответов IVA Mail CMD:
  После строки "200 OK\r\n" сервер отправляет тело ответа (JSON) как
  отдельные строки, не имеющие NNN-префикса. Тело заканчивается строкой,
  содержащей только '}' или ']', либо по таймауту (_read_body).

Использование (прямое и через ansible.builtin.script):
  python3 cmd_client.py --host 10.3.6.126 --action ping
  python3 cmd_client.py --host 10.3.6.126 --action cluster-config \\
      --backends 10.3.6.126,10.3.6.127 --own-ip 10.3.6.126
  python3 cmd_client.py --host 10.3.6.126 --action request \\
      --backends 10.3.6.126,10.3.6.127 --accounts 500 --resources 500 \\
      --name "Org RU" --name-eng "Org EN"
  python3 cmd_client.py --host 10.3.6.126 --action install \\
      --license-file /tmp/license.txt
  python3 cmd_client.py --host 10.3.6.126 --action module-read --module SMTP
  python3 cmd_client.py --host 10.3.6.126 --action domain-list
  python3 cmd_client.py --host 10.3.6.126 --action object-list --domain example.com
  python3 cmd_client.py --host 10.3.6.126 --action object-read \\
      --domain example.com --object admin
  python3 cmd_client.py --host 10.3.6.126 --action help-discover
  python3 cmd_client.py --host 10.3.6.126 --action config-dump \\
      --output-dir /tmp/dump --include-objects

Коды выхода:
  0 — успех
  1 — ошибка выполнения
  2 — ошибка использования (неверные аргументы / отсутствие env vars)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging — все сообщения в stderr, чтобы stdout оставался чистым для данных
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("IVAMAIL_DEBUG") else logging.WARNING,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("cmd_client")


DEFAULT_CONFIG_MODULES = "Cluster,CMD,SMTP,IMAP,POP3,WebAccess,AntiSpam,Antivirus"


# ---------------------------------------------------------------------------
# Исключения
# ---------------------------------------------------------------------------

class CMDError(Exception):
    """Базовое исключение протокола CMD."""


class CMDAuthError(CMDError):
    """Ошибка аутентификации (не раскрываем пароль в сообщении)."""


# ---------------------------------------------------------------------------
# Основной класс сессии
# ---------------------------------------------------------------------------

class CMDSession:
    """
    Асинхронная TCP-сессия для протокола IVA Mail CMD.

    Жизненный цикл:
      1. connect()      — установить соединение, прочитать приветствие сервера
      2. authenticate() — AUTH LOGIN → username → password
      3. <действие>     — ping / cluster_config / license_request / license_install
      4. close()        — закрыть транспорт

    Протокол ответов:
      - Однострочный:  "NNN text\\r\\n"
      - Многострочный: "NNN-text\\r\\n" ... "NNN text\\r\\n"
      - Промпты:       "Username:\\r\\n", "Password:\\r\\n"  → обрабатываются отдельно
      - 2xx — успех, 4xx — временная ошибка, 5xx — фатальная ошибка
    """

    def __init__(self) -> None:
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._timeout: float = 30.0

    # ------------------------------------------------------------------
    # Низкоуровневые методы ввода-вывода
    # ------------------------------------------------------------------

    async def connect(self, host: str, port: int, timeout: float) -> None:
        """
        Установить TCP-соединение и прочитать приветственное сообщение сервера.

        :param host: IP-адрес или FQDN цели
        :param port: TCP-порт CMD (обычно 106)
        :param timeout: таймаут сокета в секундах
        :raises CMDError: если соединение или приветствие не получено
        """
        self._timeout = timeout
        logger.debug("Connecting to %s:%s (timeout=%.1fs)", host, port, timeout)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise CMDError(f"Cannot connect to {host}:{port}: {exc}") from exc

        # Читаем приветствие: "200 hostname IVA Mail version <challenge@domain>"
        banner = await self._readline()
        logger.debug("Banner: %s", banner)
        if not banner.startswith("200"):
            raise CMDError(f"Unexpected banner from server: {banner!r}")

    async def close(self) -> None:
        """Закрыть соединение, игнорируя ошибки."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _readline(self) -> str:
        """Прочитать одну строку из сокета (без таймаута, используется внутри wait_for)."""
        assert self._reader is not None
        try:
            data = await asyncio.wait_for(self._reader.readline(), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            raise CMDError("Timeout reading from server") from exc
        if not data:
            raise CMDError("Connection closed by server")
        return data.decode("utf-8", errors="replace").rstrip("\r\n")

    async def _read_body(self) -> List[str]:
        """
        Читать тело ответа после статусной строки "200 OK".

        Протокол IVA Mail CMD: после "200 OK\\r\\n" сервер продолжает отправлять
        JSON-тело как обычные строки без NNN-префикса. Тело заканчивается:
          - строкой, содержащей ТОЛЬКО закрывающую скобку ('}' или ']')
          - пустой строкой (пустой ответ)
          - таймаутом чтения (тело прочитано частично, возвращаем накопленное)

        :returns: список строк тела (без финального разделителя)
        """
        assert self._reader is not None
        body_lines: List[str] = []
        _short_timeout = min(self._timeout, 5.0)  # короткий таймаут для тела

        while True:
            try:
                data = await asyncio.wait_for(
                    self._reader.readline(), timeout=_short_timeout
                )
            except asyncio.TimeoutError:
                # Таймаут — тело больше не идёт, возвращаем что есть
                logger.debug("_read_body: timeout after %d lines", len(body_lines))
                break

            if not data:
                # Соединение закрыто
                logger.debug("_read_body: connection closed after %d lines", len(body_lines))
                break

            line = data.decode("utf-8", errors="replace").rstrip("\r\n")
            logger.debug("<body> %s", line[:200])

            stripped = line.strip()
            if not stripped:
                # Пустая строка — возможный разделитель, если тело уже есть
                if body_lines:
                    break
                continue

            body_lines.append(line)

            # Проверяем, является ли это финальной строкой тела JSON
            # Финал: строка, заканчивающаяся на '}' или ']' (возможно с пробелами)
            if stripped in ("}", "]", "},", "],"):
                break
            # Однострочный JSON-ответ (начинается и заканчивается { } или [ ])
            if (
                (stripped.startswith("{") and stripped.endswith("}"))
                or (stripped.startswith("[") and stripped.endswith("]"))
                or stripped.startswith('"')
            ):
                break

        return body_lines

    async def _writeline(self, line: str) -> None:
        """Отправить строку, завершённую CRLF."""
        assert self._writer is not None
        self._writer.write((line + "\r\n").encode("utf-8"))
        await self._writer.drain()

    # ------------------------------------------------------------------
    # Разбор ответов протокола
    # ------------------------------------------------------------------

    async def _read_response(self) -> Tuple[int, List[str]]:
        """
        Читать ответ сервера до финальной строки.

        Финальная строка: начинается с трёх цифр, за которыми идёт пробел (не дефис).
        Промежуточные строки многострочного ответа: NNN-text.

        Особые случаи — промпты аутентификации ("Username:", "Password:"):
        возвращаются как (200, [line]).

        :returns: (код, список строк тела ответа)
        :raises CMDError: при кодах 4xx/5xx или непредвиденном формате
        """
        lines: List[str] = []
        while True:
            line = await self._readline()
            logger.debug("< %s", line)

            # Промпты аутентификации не имеют числового кода
            if re.match(r"^(Username|Password)\s*:", line, re.IGNORECASE):
                return 200, [line]

            # Стандартный ответ: NNN[- ]text
            m = re.match(r"^(\d{3})([ -])(.*)", line)
            if not m:
                # Возможно дополнительные данные после 200 OK (напр., тело ответа)
                lines.append(line)
                continue

            code = int(m.group(1))
            separator = m.group(2)
            text = m.group(3)
            lines.append(text)

            if separator == " ":
                # Финальная строка
                if code >= 500:
                    raise CMDError(f"Server fatal error {code}: {text}")
                if code >= 400:
                    raise CMDError(f"Server temporary error {code}: {text}")
                return code, lines

            # separator == "-": промежуточная строка, читаем дальше

    # ------------------------------------------------------------------
    # Аутентификация
    # ------------------------------------------------------------------

    async def authenticate(self, user: str, password: str) -> None:
        """
        Выполнить AUTH LOGIN.

        Намеренно не логирует и не включает учётные данные в текст исключений.

        :raises CMDAuthError: при отказе сервера в аутентификации
        :raises CMDError: при сетевых или протокольных ошибках
        """
        logger.debug("Sending AUTH LOGIN")
        await self._writeline("AUTH LOGIN")

        # Ожидаем промпт "Username:"
        code, resp_lines = await self._read_response()
        first = resp_lines[0] if resp_lines else ""
        if not re.match(r"^Username\s*:", first, re.IGNORECASE):
            raise CMDAuthError(
                "Unexpected response after AUTH LOGIN (expected Username prompt)"
            )

        # Отправляем имя пользователя (не секрет, но тоже не логируем детально)
        logger.debug("Sending username")
        await self._writeline(user)

        # Ожидаем промпт "Password:"
        code, resp_lines = await self._read_response()
        first = resp_lines[0] if resp_lines else ""
        if not re.match(r"^Password\s*:", first, re.IGNORECASE):
            raise CMDAuthError(
                "Unexpected response after username (expected Password prompt)"
            )

        # Отправляем пароль — ни при каких условиях не попадает в логи
        await self._writeline(password)

        # Финальный ответ аутентификации
        try:
            code, resp_lines = await self._read_response()
        except CMDError as exc:
            # Перебрасываем как CMDAuthError, чтобы вызывающий код мог отличить
            raise CMDAuthError(f"Authentication failed: {exc}") from exc

        logger.debug("Authenticated (code=%s)", code)

    # ------------------------------------------------------------------
    # Команды протокола
    # ------------------------------------------------------------------

    async def send_command(self, cmd: str) -> Tuple[int, List[str]]:
        """
        Отправить одну команду и вернуть (код, строки ответа).

        Команда не логируется на уровне INFO — чтобы не засветить пароль
        в ModuleUpdateConfig, если вызывающий код не позаботился об этом.

        :param cmd: строка команды (без завершающего CRLF)
        :returns: (int code, list[str] status_lines)
        :raises CMDError: при ошибочном коде ответа
        """
        logger.debug("> %s", cmd[:120] + ("..." if len(cmd) > 120 else ""))
        await self._writeline(cmd)
        return await self._read_response()

    async def send_command_with_body(self, cmd: str) -> Tuple[int, List[str]]:
        """
        Отправить команду, прочитать статусную строку, затем читать тело JSON-ответа.

        Используется для команд, возвращающих JSON после "200 OK":
          ModuleReadConfig, DOMAINSLIST, DOMAINREADCONFIG, OBJECTSLIST,
          OBJECTREADCONFIG, HELP, и др.

        Протокол:
          → cmd\\r\\n
          ← "200 OK\\r\\n"            (или NNN-multiline...NNN text)
          ← {JSON body lines}
          ← (пустая строка / timeout / '}')

        :returns: (code, body_lines) — body_lines включает строки JSON-тела
        :raises CMDError: при ошибочном коде ответа
        """
        logger.debug("> %s (with-body)", cmd[:120] + ("..." if len(cmd) > 120 else ""))
        await self._writeline(cmd)
        code, status_lines = await self._read_response()
        logger.debug("send_command_with_body: status=%d, status_lines=%s", code, status_lines)

        # Читаем тело только при успехе
        body_lines = await self._read_body()

        # Объединяем: если status_lines содержит что-то кроме "OK",
        # пробуем его как первую часть JSON (редкий случай)
        if body_lines:
            return code, body_lines
        # Тела нет — возвращаем status_lines (может содержать однострочный ответ)
        return code, status_lines

    async def ping(self) -> bool:
        """
        Отправить команду PING.

        :returns: True если сервер ответил 200
        :raises CMDError: при ошибке
        """
        code, lines = await self.send_command("PING")
        logger.debug("PING response: %s %s", code, lines)
        return code == 200

    async def cluster_config(
        self,
        backends: List[str],
        own_ip: str,
        port: int,
        password: str,
    ) -> None:
        """
        Отправить ModuleUpdateConfig "Cluster" с заданными параметрами.

        Формат BackendList: список строк "/I [IP]".
        Пароль передаётся в теле команды — не логируется.

        :param backends: список IP-адресов всех бэкендов
        :param own_ip: собственный IP-адрес узла
        :param port: порт CMD (обычно 106)
        :param password: пароль CMD-администратора
        :raises CMDError: при ошибке сервера
        """
        backend_list = [f"/I [{ip.strip()}]" for ip in backends]
        kv_pairs = [
            "BackendList",
            backend_list,
            "OwnAddress",
            f"/I [{own_ip}]",
            "Port",
            str(port),
            "Password",
            password,
        ]
        payload = json.dumps(kv_pairs, separators=(",", ":"), ensure_ascii=False)
        cmd = f'ModuleUpdateConfig "Cluster" [] {payload}'
        # Не логируем всю строку — содержит пароль
        logger.debug("Sending ModuleUpdateConfig Cluster (payload length=%d)", len(payload))
        await self._writeline(cmd)
        code, lines = await self._read_response()
        logger.debug("cluster_config response: %s %s", code, lines)

    async def license_request(self, params: dict) -> str:
        """
        Отправить LicenseRequest и вернуть тело запроса лицензии.

        Протокол:
          Client → LicenseRequest "-----" {JSON-params}
          Server → "200 OK"
          Server → "<quoted-escaped-content>"

        :param params: словарь параметров запроса (LicensedAccounts и др.)
        :returns: строка содержимого запроса лицензии (с реальными переносами строк)
        :raises CMDError: при ошибке сервера или неожиданном формате ответа
        """
        params_json = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        cmd = f'LicenseRequest "-----" {params_json}'
        logger.debug("Sending LicenseRequest")
        await self._writeline(cmd)

        # Первая строка: "200 OK"
        code, lines = await self._read_response()
        logger.debug("LicenseRequest ack: %s %s", code, lines)

        # Вторая строка: тело ответа — quoted escaped content
        raw_line = await self._readline()
        logger.debug("LicenseRequest body line length=%d", len(raw_line))

        return self._unescape_quoted(raw_line)

    async def license_install(self, content: str) -> None:
        """
        Установить лицензию через LicenseInstall.

        Содержимое файла экранируется: \\n → \\\\n, " → \\".

        :param content: текст лицензионного файла
        :raises CMDError: при ошибке сервера
        """
        escaped = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        cmd = f'LicenseInstall "{escaped}"'
        logger.debug("Sending LicenseInstall (content_length=%d)", len(content))
        await self._writeline(cmd)
        code, lines = await self._read_response()
        logger.debug("LicenseInstall response: %s %s", code, lines)

    async def module_read_config(self, module: str) -> dict:
        """ModuleReadConfig "Module" → parsed dict or {"_raw": lines}."""
        code, lines = await self.send_command_with_body(f'ModuleReadConfig "{module}"')
        return _parse_cmd_json(lines)

    async def module_update_config_kv(self, module: str, kv_pairs: list) -> None:
        """ModuleUpdateConfig "Module" [] [k,v,k,v,...]"""
        kv_json = json.dumps(kv_pairs, separators=(",", ":"), ensure_ascii=False)
        cmd = f'ModuleUpdateConfig "{module}" [] {kv_json}'
        logger.debug("Sending ModuleUpdateConfig %s", module)
        await self._writeline(cmd)
        await self._read_response()

    async def domain_list(self) -> list:
        """DOMAINSLIST → list of domain name strings."""
        code, lines = await self.send_command_with_body("DOMAINSLIST")
        return _parse_cmd_list(lines)

    async def domain_read_config(self, domain: str) -> dict:
        """DOMAINREADCONFIG "domain" → parsed dict or {"_raw": lines}."""
        code, lines = await self.send_command_with_body(f'DOMAINREADCONFIG "{domain}"')
        return _parse_cmd_json(lines)

    async def domain_update_config_kv(self, domain: str, kv_pairs: list) -> None:
        """DOMAINUPDATECONFIG "domain" [] [k,v,k,v,...]"""
        kv_json = json.dumps(kv_pairs, separators=(",", ":"), ensure_ascii=False)
        cmd = f'DOMAINUPDATECONFIG "{domain}" [] {kv_json}'
        logger.debug("Sending DOMAINUPDATECONFIG %s", domain)
        await self._writeline(cmd)
        await self._read_response()

    async def object_list(self, domain: str, obj_type: Optional[str] = None) -> list:
        """
        OBJECTSLIST "domain" [filter] → list of object UIDs.

        :param domain: имя домена IVA Mail
        :param obj_type: опциональный тип объекта для фильтрации
        :returns: список строк-UID объектов
        """
        if obj_type:
            cmd = f'OBJECTSLIST "{domain}" "{obj_type}"'
        else:
            cmd = f'OBJECTSLIST "{domain}"'
        code, lines = await self.send_command_with_body(cmd)
        return _parse_cmd_list(lines)

    async def object_read_config(self, domain: str, uid: str) -> dict:
        """
        OBJECTREADCONFIG "domain" "uid" → parsed dict or {"_raw": lines}.

        :param domain: имя домена
        :param uid: UID объекта (имя учётной записи, ресурса, и т.п.)
        :returns: dict конфигурации объекта
        """
        code, lines = await self.send_command_with_body(f'OBJECTREADCONFIG "{domain}" "{uid}"')
        return _parse_cmd_json(lines)

    async def object_update_config_kv(self, domain: str, uid: str, kv_pairs: list) -> None:
        """
        OBJECTUPDATECONFIG "domain" "uid" [] [k,v,k,v,...].

        :param domain: имя домена
        :param uid: UID объекта
        :param kv_pairs: плоский список чередующихся ключей и значений
        """
        kv_json = json.dumps(kv_pairs, separators=(",", ":"), ensure_ascii=False)
        cmd = f'OBJECTUPDATECONFIG "{domain}" "{uid}" [] {kv_json}'
        logger.debug("Sending OBJECTUPDATECONFIG %s@%s", uid, domain)
        await self._writeline(cmd)
        await self._read_response()

    async def help_discover(self) -> list:
        """
        HELP → список доступных команд сервера.

        Парсит ответ как JSON-массив строк или plain-text список команд.

        :returns: список строк команд
        :raises CMDError: при ошибке сервера
        """
        code, lines = await self.send_command_with_body("HELP")
        # Пробуем как JSON-массив
        text = "\n".join(lines).strip()
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return [str(x) for x in result]
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback: одна команда на строку
        commands = []
        for line in lines:
            stripped = line.strip()
            # Пропускаем числовые коды и пустые строки
            if stripped and not stripped[0].isdigit():
                commands.append(stripped)
        return commands

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    @staticmethod
    def _unescape_quoted(raw: str) -> str:
        """
        Разобрать строку в формате сервера: снять внешние кавычки,
        раскрыть escape-последовательности \\n → \\n, \\" → ", \\\\ → \\.

        :param raw: строка как пришла от сервера (возможно в кавычках)
        :returns: раскодированный текст
        """
        s = raw.strip()
        # Снять внешние двойные кавычки, если есть
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        # Раскрываем escape-последовательности в правильном порядке
        # Сначала \\ → placeholder, чтобы не сломать дальнейшие замены
        s = s.replace("\\\\", "\x00BACKSLASH\x00")
        s = s.replace('\\"', '"')
        s = s.replace("\\n", "\n")
        s = s.replace("\\r", "\r")
        s = s.replace("\x00BACKSLASH\x00", "\\")
        return s


# ---------------------------------------------------------------------------
# Вспомогательные функции для разбора ответов CMD
# ---------------------------------------------------------------------------

def _parse_cmd_json(lines: list) -> dict:
    """Parse JSON from CMD response lines; return {"_raw": lines} on failure."""
    text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return {"_value": result}
    except (json.JSONDecodeError, ValueError):
        return {"_raw": lines, "_parse_error": True}


def _parse_cmd_list(lines: list) -> list:
    """Parse a list from CMD response lines (JSON array or one item per line)."""
    text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: one domain per line, strip codes
    items = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped[0].isdigit():
            items.append(stripped)
    return items


def _dict_to_kv_pairs(d: dict) -> list:
    """Convert config dict to flat kv_pairs list for ModuleUpdateConfig."""
    pairs = []
    for k, v in d.items():
        if not str(k).startswith("_"):
            pairs.append(k)
            pairs.append(v)
    return pairs


# ---------------------------------------------------------------------------
# Основная асинхронная логика
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    """
    Выполнить запрошенное действие, управляя жизненным циклом CMDSession.

    :param args: разобранные аргументы CLI (включает user, password)
    :raises SystemExit: с кодом 1 при любой ошибке
    """
    session = CMDSession()
    try:
        await session.connect(args.host, args.port, args.timeout)
        await session.authenticate(args.user, args.password)

        if args.action == "ping":
            await _action_ping(session)

        elif args.action == "cluster-config":
            await _action_cluster_config(session, args)

        elif args.action == "request":
            await _action_request(session, args)

        elif args.action == "install":
            await _action_install(session, args)

        elif args.action == "module-read":
            await _action_module_read(session, args)

        elif args.action == "module-update":
            await _action_module_update(session, args)

        elif args.action == "domain-list":
            await _action_domain_list(session, args)

        elif args.action == "domain-read":
            await _action_domain_read(session, args)

        elif args.action == "domain-update":
            await _action_domain_update(session, args)

        elif args.action == "object-list":
            await _action_object_list(session, args)

        elif args.action == "object-read":
            await _action_object_read(session, args)

        elif args.action == "object-update":
            await _action_object_update(session, args)

        elif args.action == "help-discover":
            await _action_help_discover(session)

        elif args.action == "config-dump":
            await _action_config_dump(session, args)

        elif args.action == "config-restore":
            await _action_config_restore(session, args)

        else:
            print(f"ERROR: Unknown action: {args.action}", file=sys.stderr)
            sys.exit(2)

    except CMDAuthError as exc:
        print(f"ERROR: Authentication failed (check IVAMAIL_CMD_USER/IVAMAIL_CMD_PASSWORD)",
              file=sys.stderr)
        logger.debug("Auth error detail: %s", exc)
        sys.exit(1)
    except CMDError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await session.close()


async def _action_ping(session: CMDSession) -> None:
    """Выполнить PING и завершиться с кодом 0."""
    ok = await session.ping()
    if ok:
        print("OK: ping successful")
    else:
        print("ERROR: ping returned non-200", file=sys.stderr)
        sys.exit(1)


async def _action_cluster_config(session: CMDSession, args: argparse.Namespace) -> None:
    """
    Настроить модуль Cluster.

    Требует: --backends, --own-ip.
    """
    if not args.backends:
        print("ERROR: --backends is required for cluster-config", file=sys.stderr)
        sys.exit(2)
    if not args.own_ip:
        print("ERROR: --own-ip is required for cluster-config", file=sys.stderr)
        sys.exit(2)

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    await session.cluster_config(
        backends=backends,
        own_ip=args.own_ip,
        port=args.port,
        password=args.password,
    )
    print("OK: cluster-config applied")


async def _action_request(session: CMDSession, args: argparse.Namespace) -> None:
    """
    Отправить LicenseRequest и вывести содержимое запроса в stdout.

    Требует: --accounts, --resources, --name, --name-eng.
    Опционально: --backends (для подсчёта ClusterBackends), --cluster-backends,
                 --cluster-frontends.
    """
    missing = []
    if args.accounts is None:
        missing.append("--accounts")
    if args.resources is None:
        missing.append("--resources")
    if not args.name:
        missing.append("--name")
    if not args.name_eng:
        missing.append("--name-eng")
    if missing:
        print(f"ERROR: required for 'request': {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    # Автовычисление числа бэкендов
    cluster_backends = args.cluster_backends
    if cluster_backends is None:
        if args.backends:
            cluster_backends = len([b for b in args.backends.split(",") if b.strip()])
        else:
            cluster_backends = 1

    cluster_frontends = args.cluster_frontends if args.cluster_frontends is not None else 0

    params = {
        "LicensedAccounts": args.accounts,
        "LicensedResources": args.resources,
        "LicenseeNameRu": args.name,
        "LicenseeNameEn": args.name_eng,
        "ClusterBackends": cluster_backends,
        "ClusterFrontends": cluster_frontends,
    }

    content = await session.license_request(params)
    # Выводим содержимое запроса в stdout для захвата Ansible-ем
    print(content, end="")


async def _action_install(session: CMDSession, args: argparse.Namespace) -> None:
    """
    Установить лицензию из файла.

    Требует: --license-file.
    """
    if not args.license_file:
        print("ERROR: --license-file is required for install", file=sys.stderr)
        sys.exit(2)

    with open(args.license_file, "r", encoding="utf-8") as fh:
        content = fh.read()

    await session.license_install(content)
    print("OK: license installed")


async def _action_module_read(session: CMDSession, args: argparse.Namespace) -> None:
    """Read module config and print JSON to stdout."""
    if not args.module:
        print("ERROR: --module is required for module-read", file=sys.stderr)
        sys.exit(2)
    config = await session.module_read_config(args.module)
    print(json.dumps(config, ensure_ascii=False, indent=2))


async def _action_module_update(session: CMDSession, args: argparse.Namespace) -> None:
    """Update module config from JSON file."""
    if not args.module:
        print("ERROR: --module is required for module-update", file=sys.stderr)
        sys.exit(2)
    if not args.config_file:
        print("ERROR: --config-file is required for module-update", file=sys.stderr)
        sys.exit(2)
    with open(args.config_file, "r", encoding="utf-8") as fh:
        dump = json.load(fh)
    config = dump.get("config", dump) if isinstance(dump, dict) else {}
    kv_pairs = _dict_to_kv_pairs(config)
    if not kv_pairs:
        print(f"WARNING: empty config in {args.config_file}, skipping", file=sys.stderr)
        return
    await session.module_update_config_kv(args.module, kv_pairs)
    print(f"OK: module {args.module} updated")


async def _action_domain_list(session: CMDSession, args: argparse.Namespace) -> None:
    """List domains and print JSON array to stdout."""
    domains = await session.domain_list()
    print(json.dumps(domains, ensure_ascii=False, indent=2))


async def _action_domain_read(session: CMDSession, args: argparse.Namespace) -> None:
    """Read domain config and print JSON to stdout."""
    if not args.domain:
        print("ERROR: --domain is required for domain-read", file=sys.stderr)
        sys.exit(2)
    config = await session.domain_read_config(args.domain)
    print(json.dumps(config, ensure_ascii=False, indent=2))


async def _action_domain_update(session: CMDSession, args: argparse.Namespace) -> None:
    """Update domain config from JSON file."""
    if not args.domain:
        print("ERROR: --domain is required for domain-update", file=sys.stderr)
        sys.exit(2)
    if not args.config_file:
        print("ERROR: --config-file is required for domain-update", file=sys.stderr)
        sys.exit(2)
    with open(args.config_file, "r", encoding="utf-8") as fh:
        dump = json.load(fh)
    config = dump.get("config", dump) if isinstance(dump, dict) else {}
    kv_pairs = _dict_to_kv_pairs(config)
    if not kv_pairs:
        print(f"WARNING: empty config in {args.config_file}, skipping", file=sys.stderr)
        return
    await session.domain_update_config_kv(args.domain, kv_pairs)
    print(f"OK: domain {args.domain} updated")


async def _action_object_list(session: CMDSession, args: argparse.Namespace) -> None:
    """List objects in a domain and print JSON array to stdout."""
    if not args.domain:
        print("ERROR: --domain is required for object-list", file=sys.stderr)
        sys.exit(2)
    obj_type = getattr(args, "obj_type", None)
    objects = await session.object_list(args.domain, obj_type)
    print(json.dumps(objects, ensure_ascii=False, indent=2))


async def _action_object_read(session: CMDSession, args: argparse.Namespace) -> None:
    """Read object config and print JSON to stdout."""
    if not args.domain:
        print("ERROR: --domain is required for object-read", file=sys.stderr)
        sys.exit(2)
    if not args.object:
        print("ERROR: --object is required for object-read", file=sys.stderr)
        sys.exit(2)
    config = await session.object_read_config(args.domain, args.object)
    print(json.dumps(config, ensure_ascii=False, indent=2))


async def _action_object_update(session: CMDSession, args: argparse.Namespace) -> None:
    """Update object config from JSON file."""
    if not args.domain:
        print("ERROR: --domain is required for object-update", file=sys.stderr)
        sys.exit(2)
    if not args.object:
        print("ERROR: --object is required for object-update", file=sys.stderr)
        sys.exit(2)
    if not args.config_file:
        print("ERROR: --config-file is required for object-update", file=sys.stderr)
        sys.exit(2)
    with open(args.config_file, "r", encoding="utf-8") as fh:
        dump = json.load(fh)
    config = dump.get("config", dump) if isinstance(dump, dict) else {}
    kv_pairs = _dict_to_kv_pairs(config)
    if not kv_pairs:
        print(f"WARNING: empty config in {args.config_file}, skipping", file=sys.stderr)
        return
    await session.object_update_config_kv(args.domain, args.object, kv_pairs)
    print(f"OK: object {args.object}@{args.domain} updated")


async def _action_help_discover(session: CMDSession) -> None:
    """Send HELP and print available commands as JSON array to stdout."""
    commands = await session.help_discover()
    print(json.dumps(commands, ensure_ascii=False, indent=2))


async def _action_config_dump(session: CMDSession, args: argparse.Namespace) -> None:
    """
    Dump all key module configs (and optionally domain configs) to JSON files.

    Output directory: --output-dir (created if missing).
    Modules: --modules (comma-separated, default: DEFAULT_CONFIG_MODULES).
    Each file: module_<Name>.json or domain_<name>.json.
    Exit 0 even if some modules fail (logged to stderr); summary JSON to stdout.
    """
    import os as _os
    from datetime import datetime, timezone

    output_dir = args.output_dir
    if not output_dir:
        print("ERROR: --output-dir is required for config-dump", file=sys.stderr)
        sys.exit(2)
    _os.makedirs(output_dir, exist_ok=True)

    modules_str = args.modules if args.modules else DEFAULT_CONFIG_MODULES
    modules = [m.strip() for m in modules_str.split(",") if m.strip()]
    now = datetime.now(timezone.utc).isoformat()
    results = {"host": args.host, "dumped_at": now, "ok": [], "failed": []}

    # --- Module configs ---
    for module in modules:
        try:
            config = await session.module_read_config(module)
            dump = {
                "type": "module",
                "name": module,
                "host": args.host,
                "dumped_at": now,
                "config": config,
            }
            filepath = _os.path.join(output_dir, f"module_{module}.json")
            with open(filepath, "w", encoding="utf-8") as fh:
                json.dump(dump, fh, ensure_ascii=False, indent=2)
            results["ok"].append(f"module:{module}")
            logger.debug("Dumped module %s → %s", module, filepath)
        except CMDError as exc:
            results["failed"].append(f"module:{module}: {exc}")
            print(f"WARNING: failed to dump module {module}: {exc}", file=sys.stderr)

    # --- Domain configs (optional, best-effort) ---
    domains: list = []
    try:
        domains = await session.domain_list()
        for domain in domains:
            try:
                config = await session.domain_read_config(domain)
                dump = {
                    "type": "domain",
                    "name": domain,
                    "host": args.host,
                    "dumped_at": now,
                    "config": config,
                }
                safe_name = domain.replace("/", "_").replace(":", "_").replace("*", "_")
                filepath = _os.path.join(output_dir, f"domain_{safe_name}.json")
                with open(filepath, "w", encoding="utf-8") as fh:
                    json.dump(dump, fh, ensure_ascii=False, indent=2)
                results["ok"].append(f"domain:{domain}")
            except CMDError as exc:
                results["failed"].append(f"domain:{domain}: {exc}")
                print(f"WARNING: failed to dump domain {domain}: {exc}", file=sys.stderr)
    except CMDError as exc:
        print(f"WARNING: DOMAINSLIST failed, skipping domain dump: {exc}", file=sys.stderr)

    # --- Object configs (только при --include-objects) ---
    include_objects: bool = getattr(args, "include_objects", False)
    if include_objects and domains:
        objects_dir = _os.path.join(output_dir, "objects")
        _os.makedirs(objects_dir, exist_ok=True)
        for domain in domains:
            safe_domain = domain.replace("/", "_").replace(":", "_").replace("*", "_")
            domain_obj_dir = _os.path.join(objects_dir, safe_domain)
            try:
                obj_uids = await session.object_list(domain)
                for uid in obj_uids:
                    try:
                        obj_config = await session.object_read_config(domain, uid)
                        obj_dump = {
                            "type": "object",
                            "domain": domain,
                            "uid": uid,
                            "host": args.host,
                            "dumped_at": now,
                            "config": obj_config,
                        }
                        _os.makedirs(domain_obj_dir, exist_ok=True)
                        safe_uid = uid.replace("/", "_").replace(":", "_").replace("@", "_")
                        obj_filepath = _os.path.join(domain_obj_dir, f"object_{safe_uid}.json")
                        with open(obj_filepath, "w", encoding="utf-8") as fh:
                            json.dump(obj_dump, fh, ensure_ascii=False, indent=2)
                        results["ok"].append(f"object:{uid}@{domain}")
                        logger.debug("Dumped object %s@%s → %s", uid, domain, obj_filepath)
                    except CMDError as exc:
                        results["failed"].append(f"object:{uid}@{domain}: {exc}")
                        print(f"WARNING: failed to dump object {uid}@{domain}: {exc}", file=sys.stderr)
            except CMDError as exc:
                print(f"WARNING: OBJECTSLIST for {domain} failed, skipping: {exc}", file=sys.stderr)

    print(json.dumps(results, ensure_ascii=False))

    if results["failed"]:
        print(
            f"WARNING: {len(results['failed'])} items failed, "
            f"{len(results['ok'])} succeeded",
            file=sys.stderr,
        )


async def _action_config_restore(session: CMDSession, args: argparse.Namespace) -> None:
    """
    Restore configs from JSON files in --input-dir to the target server via CMD.

    Applies module_*.json via ModuleUpdateConfig and domain_*.json via DOMAINUPDATECONFIG.
    Skips files with _parse_error flag. Reports summary JSON to stdout.
    Exits 1 if any file fails to apply.
    """
    import os as _os
    import glob as _glob

    input_dir = args.input_dir
    if not input_dir:
        print("ERROR: --input-dir is required for config-restore", file=sys.stderr)
        sys.exit(2)
    if not _os.path.isdir(input_dir):
        print(f"ERROR: input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    files = sorted(_glob.glob(_os.path.join(input_dir, "*.json")))
    if not files:
        print(f"WARNING: no JSON files found in {input_dir}", file=sys.stderr)
        return

    results = {"host": args.host, "applied": [], "failed": [], "skipped": []}

    for filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                dump = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            results["failed"].append(f"{filepath}: read error: {exc}")
            continue

        config_type = dump.get("type")
        name = dump.get("name", "")
        config = dump.get("config", {})

        if not name or not config:
            results["skipped"].append(f"{_os.path.basename(filepath)}: missing name or config")
            continue
        if config.get("_parse_error"):
            results["skipped"].append(f"{_os.path.basename(filepath)}: parse_error flag set, skipping")
            continue

        kv_pairs = _dict_to_kv_pairs(config)
        if not kv_pairs:
            results["skipped"].append(f"{_os.path.basename(filepath)}: empty kv_pairs")
            continue

        try:
            if config_type == "module":
                await session.module_update_config_kv(name, kv_pairs)
                results["applied"].append(f"module:{name}")
            elif config_type == "domain":
                await session.domain_update_config_kv(name, kv_pairs)
                results["applied"].append(f"domain:{name}")
            else:
                results["skipped"].append(f"{_os.path.basename(filepath)}: unknown type {config_type!r}")
        except CMDError as exc:
            results["failed"].append(f"{config_type}:{name}: {exc}")
            print(f"WARNING: failed to restore {config_type}:{name}: {exc}", file=sys.stderr)

    print(json.dumps(results, ensure_ascii=False))

    if results["failed"]:
        print(
            f"ERROR: {len(results['failed'])} items failed to restore",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"OK: {len(results['applied'])} items restored, "
        f"{len(results['skipped'])} skipped",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Построить и вернуть ArgumentParser."""
    parser = argparse.ArgumentParser(
        description=(
            "CLI-клиент протокола IVA Mail CMD (TCP порт 106).\n"
            "Учётные данные: env IVAMAIL_CMD_USER, IVAMAIL_CMD_PASSWORD."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  %(prog)s --host 10.3.6.126 --action ping\n"
            "  %(prog)s --host 10.3.6.126 --action cluster-config \\\n"
            "      --backends 10.3.6.126,10.3.6.127 --own-ip 10.3.6.126\n"
            "  %(prog)s --host 10.3.6.126 --action request \\\n"
            "      --backends 10.3.6.126,10.3.6.127 \\\n"
            "      --accounts 500 --resources 500 \\\n"
            "      --name 'ООО Организация' --name-eng 'Org LLC'\n"
            "  %(prog)s --host 10.3.6.126 --action install \\\n"
            "      --license-file /tmp/license.txt\n"
        ),
    )

    # Обязательные / часто используемые
    parser.add_argument(
        "--host",
        required=True,
        metavar="HOST",
        help="IP-адрес или FQDN целевого сервера IVA Mail",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=106,
        metavar="PORT",
        help="TCP-порт CMD (по умолчанию: 106)",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=[
            "ping", "cluster-config", "request", "install",
            "module-read", "module-update", "domain-list",
            "domain-read", "domain-update",
            "object-list", "object-read", "object-update",
            "help-discover",
            "config-dump", "config-restore",
        ],
        metavar="ACTION",
        help=(
            "Действие: ping | cluster-config | request | install | "
            "module-read | module-update | domain-list | domain-read | domain-update | "
            "object-list | object-read | object-update | help-discover | "
            "config-dump | config-restore"
        ),
    )

    # cluster-config / request
    parser.add_argument(
        "--backends",
        metavar="IP1,IP2,...",
        help="Запятая-разделённый список IP всех бэкендов (для cluster-config и request)",
    )
    parser.add_argument(
        "--own-ip",
        metavar="IP",
        help="Собственный IP узла для OwnAddress (для cluster-config)",
    )

    # request
    parser.add_argument(
        "--accounts",
        type=int,
        metavar="N",
        help="LicensedAccounts — количество лицензированных аккаунтов",
    )
    parser.add_argument(
        "--resources",
        type=int,
        metavar="N",
        help="LicensedResources — количество лицензированных ресурсов",
    )
    parser.add_argument(
        "--name",
        metavar="TEXT",
        help="LicenseeNameRu — наименование лицензиата (кириллица)",
    )
    parser.add_argument(
        "--name-eng",
        metavar="TEXT",
        dest="name_eng",
        help="LicenseeNameEn — наименование лицензиата (латиница)",
    )
    parser.add_argument(
        "--cluster-backends",
        type=int,
        metavar="N",
        dest="cluster_backends",
        help=(
            "Количество бэкендов кластера для запроса лицензии "
            "(по умолчанию: авто из --backends)"
        ),
    )
    parser.add_argument(
        "--cluster-frontends",
        type=int,
        default=0,
        metavar="N",
        dest="cluster_frontends",
        help="Количество фронтендов кластера (по умолчанию: 0)",
    )

    # install
    parser.add_argument(
        "--license-file",
        metavar="PATH",
        dest="license_file",
        help="Путь к файлу лицензии .txt (для action=install)",
    )

    # config management
    parser.add_argument(
        "--module",
        metavar="NAME",
        help="Имя модуля IVA Mail (для module-read/module-update)",
    )
    parser.add_argument(
        "--domain",
        metavar="DOMAIN",
        help="Имя домена IVA Mail (для domain-read/domain-update/object-list/object-read/object-update)",
    )
    parser.add_argument(
        "--object",
        metavar="UID",
        help="UID объекта домена (для object-read/object-update)",
    )
    parser.add_argument(
        "--obj-type",
        metavar="TYPE",
        dest="obj_type",
        help="Тип объекта для фильтрации в OBJECTSLIST (для object-list)",
    )
    parser.add_argument(
        "--config-file",
        metavar="PATH",
        dest="config_file",
        help="Путь к JSON-файлу конфигурации (для module-update/domain-update/object-update)",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        dest="output_dir",
        help="Каталог для сохранения JSON-дампов (для config-dump)",
    )
    parser.add_argument(
        "--input-dir",
        metavar="DIR",
        dest="input_dir",
        help="Каталог с JSON-файлами для восстановления (для config-restore)",
    )
    parser.add_argument(
        "--modules",
        metavar="M1,M2,...",
        default=None,
        help=(
            f"Список модулей для дампа через запятую "
            f"(по умолчанию: {DEFAULT_CONFIG_MODULES})"
        ),
    )
    parser.add_argument(
        "--include-objects",
        action="store_true",
        dest="include_objects",
        default=False,
        help="Включить дамп объектов доменов (для config-dump; --include-objects)",
    )

    # Общие
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SEC",
        help="Таймаут сокета в секундах (по умолчанию: 30)",
    )

    return parser


def main() -> None:
    """
    Точка входа: разобрать аргументы, проверить env vars, запустить asyncio.

    Завершается с кодом 2 при неверных аргументах / отсутствии env vars,
    с кодом 1 при ошибке выполнения, с кодом 0 при успехе.
    """
    parser = build_parser()
    args = parser.parse_args()

    # Учётные данные строго из env vars — никогда из CLI
    user = os.environ.get("IVAMAIL_CMD_USER", "").strip()
    password = os.environ.get("IVAMAIL_CMD_PASSWORD", "").strip()

    if not user or not password:
        print(
            "ERROR: env vars IVAMAIL_CMD_USER and IVAMAIL_CMD_PASSWORD are required",
            file=sys.stderr,
        )
        sys.exit(2)

    # Прикрепляем к args для передачи в run() — без записи в лог
    args.user = user
    args.password = password

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
