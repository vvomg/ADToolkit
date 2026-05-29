import argparse
import csv
import html
import imaplib
import json
import os
import re
import socket
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ==============================================================================
# Defaults. These can be overridden with a JSON config file or CLI arguments.
# ==============================================================================
DEFAULT_HOST = os.getenv("IVA_CMD_HOST", "server.msk")
DEFAULT_PORT = int(os.getenv("IVA_CMD_PORT", "106"))
DEFAULT_USER = os.getenv("IVA_CMD_USER", "admin")
DEFAULT_PASSWORD = os.getenv("IVA_CMD_PASSWORD", "somepass")
DEFAULT_OUTPUT = "mailbox_inventory.json"
# ==============================================================================


CONFIG_KEYS = {
    "host",
    "port",
    "user",
    "password",
    "domains",
    "all_domains",
    "accounts",
    "output",
    "input_json",
    "html_output",
    "report_title",
    "include_imap_stats",
    "imap_host",
    "imap_port",
    "imap_ssl",
    "imap_user",
    "imap_password",
    "imap_workers",
    "format",
    "timeout",
    "cmd_workers",
    "page_size",
    "max_domains",
    "max_accounts_per_domain",
    "max_mailboxes_per_account",
    "mailbox_class",
    "include_acl",
    "include_raw",
    "recalculate_storage",
    "include_non_mail_objects",
    "include_account_config",
    "include_mailbox_info",
    "account_storage_source",
}

DOMAIN_INTRINSIC_KEYS = {"type", "names", "IPlist", "SecureContexts", "AccountDefaults", "QueueRules", "Rules"}
ACCOUNT_INTRINSIC_KEYS = {"names", "type", "Password", "UID", "uid", "CreateTime", "CreationTime", "AccountACL", "QueueRules", "Rules"}

OBJECT_KEYS = [
    "name",
    "RealName",
    "DisplayName",
    "Owner",
    "StorageSize",
    "MailStorageSize",
    "MailStorageQuota",
    "MaxMailStorageSize",
    "MaxStorageSize",
    "Quota",
    "DiskQuota",
    "Created",
    "CreateTime",
    "CreationTime",
    "Server",
    "HomeServer",
    "StorageServer",
    "Type",
    "ObjectType",
    "AccountACL",
    "QueueRules",
]

ALIASES = {
    "uid": ["UID", "Uid", "uid", "ID", "Id", "id", "MboxUID", "MailboxUID"],
    "created_at": [
        "Created",
        "CreateTime",
        "CreationTime",
        "CreatedAt",
        "CreationDate",
        "ctime",
    ],
    "owner": ["Owner", "owner", "Creator", "CreatedBy"],
    "message_count": [
        "MessageCount",
        "MessagesCount",
        "Messages",
        "ItemCount",
        "ItemsCount",
        "Items",
        "Count",
    ],
    "quota": [
        "MailStorageQuota",
        "MaxMailStorageSize",
        "MaxStorageSize",
        "StorageQuota",
        "DiskQuota",
        "MailboxQuota",
        "MailQuota",
        "Quota",
        "MaxSize",
        "Limit",
    ],
    "size": [
        "StorageSize",
        "MailStorageSize",
        "UsedStorage",
        "UsedSize",
        "CurrentSize",
        "Size",
        "Bytes",
    ],
    "server": [
        "HomeServer",
        "StorageServer",
        "Server",
        "ServerName",
        "ClusterNode",
        "Node",
        "Host",
    ],
    "type": ["Type", "ObjectType", "Class", "ObjectClass"],
    "name": ["Name", "FullName", "MailboxName", "RealName", "DisplayName"],
}

NOT_AVAILABLE = "N/A"


class CmdError(RuntimeError):
    def __init__(self, command: str, status: str, body: Any = None):
        super().__init__(f"{command}: {status}")
        self.command = command
        self.status = status
        self.body = body


def q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def is_ok(status: str) -> bool:
    return status.startswith("200") or status.startswith("204")


def non_empty(value: Any) -> bool:
    return value is not None and value != ""


def value_or_na(value: Any) -> Any:
    return value if non_empty(value) else NOT_AVAILABLE


def human_size(value: Any) -> str:
    if value is None or value == "":
        return NOT_AVAILABLE
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value)
    if size < 0:
        return "unlimited"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.2f} {units[index]}"


def normalize_quota(value: Any) -> Any:
    if isinstance(value, list):
        for item in value:
            if item is not None:
                return item
        return None
    return value


def load_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a JSON object")
    unknown = sorted(set(data) - CONFIG_KEYS)
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(unknown)}")
    return data


def merge_args(args: argparse.Namespace, config: Dict[str, Any]) -> argparse.Namespace:
    defaults = {
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "user": DEFAULT_USER,
        "password": DEFAULT_PASSWORD,
        "domains": [],
        "all_domains": False,
        "accounts": [],
        "output": DEFAULT_OUTPUT,
        "input_json": None,
        "html_output": None,
        "report_title": "IVA Mail folder inventory",
        "include_imap_stats": True,
        "imap_host": None,
        "imap_port": None,
        "imap_ssl": False,
        "imap_user": None,
        "imap_password": None,
        "imap_workers": 4,
        "format": "json",
        "timeout": 60.0,
        "cmd_workers": 4,
        "page_size": 1000,
        "max_domains": None,
        "max_accounts_per_domain": None,
        "max_mailboxes_per_account": None,
        "mailbox_class": "mail",
        "include_acl": True,
        "include_raw": False,
        "recalculate_storage": False,
        "include_non_mail_objects": True,
        "include_account_config": False,
        "include_mailbox_info": False,
        "account_storage_source": "cmd",
    }

    merged = dict(defaults)
    merged.update(config)

    for key in defaults:
        value = getattr(args, key, None)
        if value is not None:
            merged[key] = value

    # Boolean flags are tri-state in argparse: None means "not specified".
    for key in [
        "include_acl",
        "include_raw",
        "recalculate_storage",
        "include_non_mail_objects",
        "include_imap_stats",
        "imap_ssl",
        "include_account_config",
        "include_mailbox_info",
    ]:
        value = getattr(args, key, None)
        if value is not None:
            merged[key] = value

    if getattr(args, "domains", None) and getattr(args, "all_domains", None) is None:
        merged["all_domains"] = False

    return argparse.Namespace(**merged)


class CmdClient:
    def __init__(self, host: str, port: int, timeout: float):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.f_in = None
        self.f_out = None

    def connect(self) -> str:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))
        self.f_in = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self.f_out = self.sock.makefile("w", encoding="utf-8", newline="\n")
        return self._readline_required()

    def close(self) -> None:
        try:
            if self.f_out and self.f_in:
                self.send_status("QUIT")
        except Exception:
            pass
        if self.sock:
            self.sock.close()

    def authenticate(self, user: str, password: str) -> str:
        self.send_status("AUTH LOGIN")
        self.send_status(user)
        status = self.send_status(password)
        if not status.startswith("200"):
            raise CmdError("AUTH LOGIN", status)
        inline_status = self.send_status("INLINE")
        if not is_ok(inline_status):
            raise CmdError("INLINE", inline_status)
        return status

    def send_status(self, command: str) -> str:
        self._write(command)
        return self._readline_required()

    def send_json(self, command: str) -> Any:
        self._write(command)
        status = self._readline_required()
        if not is_ok(status):
            raise CmdError(command, status)
        if status.startswith("204"):
            return None
        return self._read_json_body(command, status)

    def _write(self, command: str) -> None:
        if self.f_out is None:
            raise RuntimeError("Not connected")
        self.f_out.write(command + "\n")
        self.f_out.flush()

    def _readline_required(self) -> str:
        if self.f_in is None:
            raise RuntimeError("Not connected")
        line = self.f_in.readline()
        if line == "":
            raise EOFError("CMD connection closed by server")
        return line.rstrip("\r\n")

    def _read_json_body(self, command: str, status: str) -> Any:
        lines: List[str] = []
        last_error: Optional[json.JSONDecodeError] = None

        for _ in range(100000):
            lines.append(self._readline_required())
            text = "\n".join(lines).strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                last_error = e
                if self._looks_complete_json(text):
                    break

        raise CmdError(
            command,
            status,
            {
                "error": f"Failed to parse JSON response: {last_error}",
                "body_start": "\n".join(lines[:10]),
            },
        )

    @staticmethod
    def _looks_complete_json(text: str) -> bool:
        if not text:
            return False
        opens = {"{": "}", "[": "]"}
        first = text[0]
        if first in opens:
            return text[-1] == opens[first]
        if first == '"':
            return len(text) > 1 and text[-1] == '"'
        return text in ("true", "false", "null") or text[-1].isdigit()


class CmdClientPool:
    def __init__(self, host: str, port: int, timeout: float, user: str, password: str, workers: int):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.user = user
        self.password = password
        self.workers = max(1, int(workers))
        self.executor = ThreadPoolExecutor(max_workers=self.workers)
        self.local = threading.local()
        self.lock = threading.Lock()
        self.clients: List[CmdClient] = []

    def _get_client(self) -> CmdClient:
        client = getattr(self.local, "client", None)
        if client is not None:
            return client
        client = CmdClient(self.host, self.port, self.timeout)
        client.connect()
        client.authenticate(self.user, self.password)
        self.local.client = client
        with self.lock:
            self.clients.append(client)
        return client

    def _run(self, fn: Any, *args: Any) -> Any:
        return fn(self._get_client(), *args)

    def submit(self, fn: Any, *args: Any):
        return self.executor.submit(self._run, fn, *args)

    def close(self) -> None:
        self.executor.shutdown(wait=True)
        with self.lock:
            clients = list(self.clients)
            self.clients.clear()
        for client in clients:
            client.close()


def imap_utf7_encode(value: str) -> str:
    result: List[str] = []
    buf: List[str] = []

    def flush_buf() -> None:
        if not buf:
            return
        import base64

        raw = "".join(buf).encode("utf-16be")
        encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        result.append("&" + encoded + "-")
        buf.clear()

    for ch in value:
        code = ord(ch)
        if 0x20 <= code <= 0x7E:
            flush_buf()
            result.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush_buf()
    return "".join(result)


def quote_imap_mailbox(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_imap_status(data: Any) -> Dict[str, int]:
    if isinstance(data, list):
        text = b" ".join(item for item in data if isinstance(item, bytes)).decode(
            "utf-8", "replace"
        )
    elif isinstance(data, bytes):
        text = data.decode("utf-8", "replace")
    else:
        text = str(data or "")
    return {key.lower(): int(value) for key, value in re.findall(r"([A-Z]+)\s+(\d+)", text)}


def parse_imap_internaldate(value: str) -> Optional[str]:
    try:
        parsed = datetime.strptime(value, "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp_to_utc(value: Any) -> Optional[str]:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    now = datetime.now(timezone.utc).timestamp()
    if timestamp < 946684800 or timestamp > now + 366 * 24 * 60 * 60:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


class ImapStatsClient:
    def __init__(self, host: str, port: int, use_ssl: bool, user: str, password: str):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.user = user
        self.password = password
        self.conn = None

    def connect(self) -> None:
        if self.use_ssl:
            self.conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
        else:
            self.conn = imaplib.IMAP4(self.host, self.port, timeout=30)
        self.conn.login(self.user, self.password)

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            self.conn.logout()
        except Exception:
            pass

    def mailbox_stats(self, domain: str, account: str, mailbox_name: str) -> Dict[str, Any]:
        if self.conn is None:
            raise RuntimeError("IMAP is not connected")
        mailbox = quote_imap_mailbox(imap_utf7_encode(f"~{account}@{domain}/{mailbox_name}"))
        typ, status_data = self.conn.status(mailbox, "(MESSAGES UIDNEXT UIDVALIDITY UNSEEN)")
        if typ != "OK":
            raise RuntimeError(f"IMAP STATUS failed: {status_data}")
        status = parse_imap_status(status_data)
        message_count = status.get("messages", 0)
        created_at = timestamp_to_utc(status.get("uidvalidity"))
        stats: Dict[str, Any] = {
            "message_count": message_count,
            "disk_size_used_bytes": 0,
            "created_at": created_at or NOT_AVAILABLE,
            "created_at_source": "imap_uidvalidity_timestamp" if created_at else NOT_AVAILABLE,
            "earliest_message_at": NOT_AVAILABLE,
            "imap_status": status,
        }
        if message_count <= 0:
            return stats

        typ, _ = self.conn.select(mailbox, readonly=True)
        if typ != "OK":
            raise RuntimeError("IMAP SELECT failed")
        typ, fetch_data = self.conn.uid("FETCH", "1:*", "(UID RFC822.SIZE INTERNALDATE)")
        if typ != "OK":
            raise RuntimeError(f"IMAP FETCH failed: {fetch_data}")

        total_size = 0
        earliest: Optional[str] = None
        for item in fetch_data:
            if not isinstance(item, bytes):
                continue
            text = item.decode("utf-8", "replace")
            size_match = re.search(r"RFC822\.SIZE\s+(\d+)", text)
            if size_match:
                total_size += int(size_match.group(1))
            date_match = re.search(r'INTERNALDATE\s+"([^"]+)"', text)
            if date_match:
                date_value = parse_imap_internaldate(date_match.group(1))
                if date_value and (earliest is None or date_value < earliest):
                    earliest = date_value

        stats["disk_size_used_bytes"] = total_size
        stats["earliest_message_at"] = earliest or NOT_AVAILABLE
        if stats["created_at"] == NOT_AVAILABLE and earliest:
            stats["created_at"] = earliest
            stats["created_at_source"] = "imap_earliest_internaldate"
        return stats


class ImapStatsPool:
    def __init__(
        self,
        host: str,
        port: int,
        use_ssl: bool,
        user: str,
        password: str,
        workers: int,
    ):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.user = user
        self.password = password
        self.workers = max(1, int(workers))
        self.executor = ThreadPoolExecutor(max_workers=self.workers)
        self.local = threading.local()
        self.lock = threading.Lock()
        self.clients: List[ImapStatsClient] = []

    def _get_client(self) -> ImapStatsClient:
        client = getattr(self.local, "client", None)
        if client is not None:
            return client
        client = ImapStatsClient(self.host, self.port, self.use_ssl, self.user, self.password)
        client.connect()
        self.local.client = client
        with self.lock:
            self.clients.append(client)
        return client

    def _mailbox_stats(self, domain: str, account: str, mailbox_name: str) -> Dict[str, Any]:
        return self._get_client().mailbox_stats(domain, account, mailbox_name)

    def submit(self, domain: str, account: str, mailbox_name: str):
        return self.executor.submit(self._mailbox_stats, domain, account, mailbox_name)

    def close(self) -> None:
        self.executor.shutdown(wait=True)
        with self.lock:
            clients = list(self.clients)
            self.clients.clear()
        for client in clients:
            client.close()


def find_first(data: Any, aliases: Iterable[str]) -> Any:
    aliases_list = list(aliases)
    wanted = {a.lower() for a in aliases_list}

    if isinstance(data, dict):
        for alias in aliases_list:
            if alias in data:
                return data[alias]
        for key, value in data.items():
            if str(key).lower() in wanted:
                return value
        for key in ("Info", "Metadata", "Settings", "Config", "Values", "Data"):
            if key in data:
                value = find_first(data[key], aliases_list)
                if value is not None:
                    return value
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = find_first(value, aliases_list)
                if found is not None:
                    return found
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, (dict, list)):
                found = find_first(value, aliases_list)
                if found is not None:
                    return found
    return None


def normalize_mapping(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {str(i): item for i, item in enumerate(data)}
    return {}


def iter_named_items(data: Any) -> Iterable[Tuple[Optional[str], Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            yield str(key), value
    elif isinstance(data, list):
        for value in data:
            name = find_first(value, ALIASES["name"]) if isinstance(value, dict) else None
            yield str(name) if name else None, value


def object_is_mail_account(value: Any) -> bool:
    if not isinstance(value, dict):
        return True
    object_type = find_first(value, ALIASES["type"])
    if object_type is None:
        return True
    text = str(object_type).lower()
    if "account" in text:
        return True
    return not any(marker in text for marker in ("group", "resource", "forwarder"))

def get_object_type(value: Any) -> str:
    if not isinstance(value, dict):
        return "account"
    object_type = find_first(value, ALIASES["type"])
    if object_type is None:
        return "account"
    text = str(object_type).lower()
    if "group" in text:
        return "group"
    if "resource" in text:
        return "resource"
    if "forwarder" in text:
        return "forwarder"
    return "account"


def get_domains(client: CmdClient, requested: List[str], all_domains: bool) -> Dict[str, str]:
    if requested and not all_domains:
        result = {}
        for domain in requested:
            result[domain] = domain
        return result

    data = client.send_json("DOMAINSLIST")
    domains = normalize_mapping(data)
    return {str(uid): str(name) for uid, name in domains.items()}


def get_objects(client: CmdClient, domain: str, page_size: int) -> Dict[str, Any]:
    if page_size <= 0:
        return normalize_mapping(client.send_json(f"OBJECTSLIST {q(domain)}"))

    result: Dict[str, Any] = {}
    start = 0
    while True:
        fields = j(OBJECT_KEYS)
        command = f"OBJECTSLIST {q(domain)} {q('')} {start} {page_size} {fields}"
        page = normalize_mapping(client.send_json(command))
        if not page:
            break
        result.update(page)

        numeric_keys = [int(k) for k in page if str(k).isdigit()]
        if not numeric_keys or len(page) < page_size:
            break
        start = max(numeric_keys) + 1

    return result


def account_name_from_object(uid: str, value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = find_first(value, ["name", "Name", "Login", "AccountName", "RealName"])
        if name is not None:
            return str(name)
    return str(uid)


def get_account_config(client: CmdClient, domain: str, account: str) -> Dict[str, Any]:
    keys = j(OBJECT_KEYS)
    try:
        data = client.send_json(f"ObjectGetMultiset {q(domain)} {q(account)} {keys}")
    except CmdError:
        data = client.send_json(f"OBJECTREADCONFIG {q(domain)} {q(account)} false")
    return normalize_mapping(data)


def get_account_storage_size(
    client: CmdClient, domain: str, account: str, recalculate: bool
) -> Any:
    flag = "true" if recalculate else "false"
    return client.send_json(f"AccountGetMailStorageSize {q(domain)} {q(account)} {flag}")


def get_account_quota(client: CmdClient, domain: str, account: str) -> Any:
    try:
        data = client.send_json(f"OBJECTGETSETTING {q(domain)} {q(account)} {q('MailStorageQuota')} true")
    except CmdError:
        data = find_first(
            normalize_mapping(client.send_json(f"OBJECTREADCONFIG {q(domain)} {q(account)} true")),
            ["MailStorageQuota"],
        )
    return normalize_quota(data)


def get_account_acl(client: CmdClient, domain: str, account: str) -> Any:
    return client.send_json(f"OBJECTGETSETTING {q(domain)} {q(account)} {q('AccountACL')} true")


def get_rules(client: CmdClient, domain: Optional[str], account: Optional[str]) -> List[Dict[str, Any]]:
    domain_arg = "null" if domain is None else q(domain)
    account_arg = "null" if account is None else q(account)
    rules = client.send_json(f"RulesList {domain_arg} {account_arg}")
    result: List[Dict[str, Any]] = []
    for rule_name, rule_value in iter_named_items(rules):
        rule: Dict[str, Any]
        if isinstance(rule_value, dict):
            rule = dict(rule_value)
        else:
            rule = {"value": rule_value}
        if rule_name is not None and "name" not in rule:
            rule["name"] = rule_name
        name = rule.get("name")
        if name:
            try:
                details = client.send_json(f"RuleGet {domain_arg} {account_arg} {q(str(name))}")
                if isinstance(details, dict):
                    rule.update(details)
                else:
                    rule["details"] = details
            except Exception as e:
                rule["details_error"] = str(e)
        result.append(rule)
    return result


def get_mailboxes(
    client: CmdClient, domain: str, account: str, mailbox_class: Optional[str]
) -> Dict[str, Any]:
    try:
        if mailbox_class:
            command = f"MAILBOXESLIST {q(domain)} {q(account)} {q(mailbox_class)}"
        else:
            command = f"MAILBOXESLIST {q(domain)} {q(account)}"
        return normalize_mapping(client.send_json(command))
    except Exception:
        return {}


def get_mailbox_info(
    client: CmdClient, domain: str, account: str, mailbox_name: str
) -> Dict[str, Any]:
    return normalize_mapping(
        client.send_json(f"MboxGetInfo {q(domain)} {q(account)} {q(mailbox_name)}")
    )


def get_account_namespaces(client: CmdClient, domain: str, account: str) -> Any:
    return client.send_json(f"AccountListNamespaces {q(domain)} {q(account)}")


def acl_is_empty(value: Any) -> bool:
    return value is None or value == {} or value == []


def get_mailbox_acl(client: CmdClient, domain: str, account: str, mailbox_name: str) -> Any:
    if mailbox_name:
        full_name = f"~{account}@{domain}/{mailbox_name}"
        candidates = [full_name, mailbox_name]
    else:
        candidates = [f"~{account}@{domain}/", ""]
    last_value = None
    last_error = None
    for candidate in candidates:
        try:
            value = client.send_json(f"MailboxGetACL {q(domain)} {q(account)} {q(candidate)}")
        except Exception as e:
            last_error = e
            continue
        last_value = value
        if not acl_is_empty(value):
            return value
    if last_error is not None and last_value is None:
        raise last_error
    return last_value


def delegated_namespaces(namespaces: Any) -> List[str]:
    if isinstance(namespaces, dict):
        values = namespaces.keys()
    elif isinstance(namespaces, list):
        values = namespaces
    elif non_empty(namespaces):
        values = [namespaces]
    else:
        values = []
    result = []
    for value in values:
        text = str(value)
        if text and text.startswith("~"):
            result.append(text)
    return sorted(result)


MAILBOX_RIGHTS = {
    "l": ("read", "папка видна в списке папок"),
    "r": ("read", "чтение статуса папки, списка писем, писем, флагов и атрибутов"),
    "s": ("write", "изменение флага Seen"),
    "w": ("write", "изменение флагов и атрибутов писем, кроме Seen и Deleted"),
    "i": ("write", "добавление или копирование писем в папку"),
    "p": ("delivery", "доставка письма в папку, обычно не используется"),
    "k": ("folder_management", "создание вложенных папок"),
    "x": ("folder_management", "удаление или переименование папки"),
    "t": ("write", "изменение флага Deleted"),
    "e": ("write", "окончательное удаление писем из папки"),
    "a": ("administration", "чтение и изменение ACL папки"),
}

ACCOUNT_RIGHTS = {
    "r": ("read", "чтение данных аккаунта; папки изначально получают права l и r"),
    "s": ("send", "отправка от имени аккаунта с исходным отправителем в Sender"),
    "i": ("send", "имперсонация и отправка от имени аккаунта; включает право s"),
    "w": ("administration", "администрирование аккаунта; папки изначально получают все права"),
}

MAILBOX_RIGHT_ALIASES = {
    "c": "kx",
    "d": "et",
}

RIGHT_CATEGORY_LABELS = {
    "read": "Чтение",
    "write": "Запись",
    "send": "Отправка/делегирование",
    "folder_management": "Управление папкой",
    "administration": "Администрирование",
    "delivery": "Доставка/прочее",
    "unknown": "Неизвестно",
}

SPECIAL_IDENTIFIERS = {
    "-owner": "отнимает права у владельца объекта",
    "anyone": "любой аккаунт сервера, кроме владельца",
    "anyone@": "любой аккаунт текущего домена, кроме владельца",
}


def decode_rights_string(
    rights: Any,
    rights_map: Dict[str, Tuple[str, str]],
    aliases_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    raw = "" if rights is None else str(rights)
    expanded = ""
    aliases: Dict[str, str] = {}
    aliases_map = aliases_map or {}
    for char in raw:
        replacement = aliases_map.get(char)
        if replacement:
            aliases[char] = replacement
            expanded += replacement
        else:
            expanded += char

    categories: Dict[str, List[Dict[str, str]]] = {
        key: [] for key in RIGHT_CATEGORY_LABELS
    }
    seen = set()
    unknown = []
    for char in expanded:
        if char in seen:
            continue
        seen.add(char)
        detail = rights_map.get(char)
        if detail is None:
            unknown.append(char)
            categories["unknown"].append({"code": char, "description": "unknown right"})
            continue
        category, description = detail
        categories[category].append({"code": char, "description": description})

    categories = {key: value for key, value in categories.items() if value}
    return {
        "raw": raw,
        "expanded": "".join(sorted(seen, key=lambda c: expanded.index(c))),
        "aliases": aliases,
        "categories": categories,
        "unknown": unknown,
    }


def decode_acl(
    acl: Any,
    rights_map: Dict[str, Tuple[str, str]],
    aliases_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(acl, dict):
        return []
    decoded = []
    for identifier, rights in sorted(acl.items(), key=lambda item: str(item[0]).lstrip("-+").lower()):
        identifier_text = str(identifier)
        normalized = identifier_text[1:] if identifier_text.startswith("+") else identifier_text
        effect = "subtract" if identifier_text.startswith("-") else "grant"
        decoded.append(
            {
                "identifier": identifier_text,
                "principal": normalized[1:] if normalized.startswith("-") else normalized,
                "effect": effect,
                "identifier_note": SPECIAL_IDENTIFIERS.get(identifier_text, SPECIAL_IDENTIFIERS.get(normalized, "")),
                "rights": decode_rights_string(rights, rights_map, aliases_map),
            }
        )
    return decoded


def decode_mailbox_acl(acl: Any) -> List[Dict[str, Any]]:
    return decode_acl(acl, MAILBOX_RIGHTS, MAILBOX_RIGHT_ALIASES)


def decode_account_acl(acl: Any) -> List[Dict[str, Any]]:
    return decode_acl(acl, ACCOUNT_RIGHTS)


def build_mailbox_record(
    domain: str,
    account: str,
    account_uid: str,
    account_object: Any,
    account_config: Dict[str, Any],
    account_quota: Any,
    mailbox_name: str,
    mailbox_list_value: Any,
    mailbox_info: Dict[str, Any],
    mailbox_stats: Optional[Dict[str, Any]],
    acl: Any,
    account_root_acl: Any,
    include_raw: bool,
) -> Dict[str, Any]:
    mailbox_uid = (
        find_first(mailbox_info, ALIASES["uid"])
        or find_first(mailbox_list_value, ALIASES["uid"])
        or (mailbox_list_value if isinstance(mailbox_list_value, (int, str)) else None)
    )
    owner = (
        find_first(mailbox_info, ALIASES["owner"])
        or find_first(account_config, ALIASES["owner"])
        or f"{account}@{domain}"
    )
    quota = account_quota if account_quota is not None else find_first(account_config, ALIASES["quota"])
    size = find_first(mailbox_info, ALIASES["size"])
    if size is None:
        size = find_first(mailbox_list_value, ALIASES["size"])
    stats = mailbox_stats or {}
    size_bytes = stats.get("disk_size_used_bytes")
    if size_bytes is None:
        size_bytes = size if size is not None else None
    message_count = stats.get("message_count")
    if message_count is None:
        message_count = find_first(mailbox_info, ALIASES["message_count"]) or find_first(
            mailbox_list_value, ALIASES["message_count"]
        )
    created_at = stats.get("created_at")
    if not non_empty(created_at) or created_at == NOT_AVAILABLE:
        created_at = (
            find_first(mailbox_info, ALIASES["created_at"])
            or find_first(account_config, ALIASES["created_at"])
            or find_first(account_object, ALIASES["created_at"])
        )
    quota = normalize_quota(quota)
    access_rights = acl if not acl_is_empty(acl) else account_root_acl
    access_rights_source = "folder" if not acl_is_empty(acl) else (
        "account_root" if not acl_is_empty(account_root_acl) else "none"
    )

    record: Dict[str, Any] = {
        "folder_uid": value_or_na(mailbox_uid),
        "folder_name": mailbox_name,
        "mailbox_uid": value_or_na(mailbox_uid),
        "mailbox_name": mailbox_name,
        "created_at": value_or_na(created_at),
        "created_at_source": stats.get("created_at_source", NOT_AVAILABLE)
        if non_empty(stats.get("created_at"))
        and stats.get("created_at") != NOT_AVAILABLE
        else NOT_AVAILABLE,
        "earliest_message_at": stats.get("earliest_message_at", NOT_AVAILABLE),
        "owner": owner,
        "access_rights": access_rights if access_rights is not None else {},
        "access_rights_decoded": decode_mailbox_acl(access_rights),
        "access_rights_source": access_rights_source,
        "message_count": value_or_na(message_count),
        "disk_quota_max": human_size(quota),
        "disk_quota_max_bytes": quota if isinstance(quota, (int, float)) and quota >= 0 else NOT_AVAILABLE,
        "disk_size_used": human_size(size_bytes) if size_bytes is not None else NOT_AVAILABLE,
        "disk_size_used_bytes": size_bytes if size_bytes is not None else NOT_AVAILABLE,
        "server": value_or_na(find_first(account_config, ALIASES["server"])
        or find_first(mailbox_info, ALIASES["server"])
        or find_first(account_object, ALIASES["server"])),
        "domain": domain,
        "account": account,
        "account_uid": account_uid,
        "notes": {
            "disk_size_used": ""
            if size_bytes is not None
            else "not exposed by available CMD/IMAP methods",
            "message_count": ""
            if message_count is not None
            else "not exposed by available CMD/IMAP methods",
            "created_at": ""
            if non_empty(created_at) and created_at != NOT_AVAILABLE
            else "not exposed by available CMD/IMAP methods",
        },
    }

    if include_raw:
        record["raw"] = {
            "account_object": account_object,
            "account_config": account_config,
            "mailbox_list_value": mailbox_list_value,
            "mailbox_info": mailbox_info,
            "mailbox_stats": mailbox_stats,
            "mailbox_acl": acl,
            "account_root_acl": account_root_acl,
        }

    return record


def build_account_summary(
    domain: str,
    account: str,
    account_uid: str,
    account_object: Any,
    account_config: Dict[str, Any],
    account_storage: Any,
    account_quota: Any,
    mailbox_count: int,
    account_root_acl: Any,
    account_acl: Any,
    account_namespaces: Any,
    account_rules: List[Dict[str, Any]],
    explicit_settings: Dict[str, Any],
    is_admin: bool,
) -> Dict[str, Any]:
    quota = normalize_quota(
        account_quota if account_quota is not None else find_first(account_config, ALIASES["quota"])
    )
    return {
        "domain": domain,
        "account": account,
        "account_uid": account_uid,
        "folder_count": mailbox_count,
        "mailbox_count": mailbox_count,
        "owner": value_or_na(find_first(account_config, ALIASES["owner"]) or f"{account}@{domain}"),
        "created_at": value_or_na(
            find_first(account_config, ALIASES["created_at"])
            or find_first(account_object, ALIASES["created_at"])
        ),
        "disk_size_used": human_size(account_storage),
        "disk_size_used_bytes": account_storage
        if isinstance(account_storage, (int, float))
        else NOT_AVAILABLE,
        "disk_quota_max": human_size(quota),
        "disk_quota_max_bytes": quota
        if isinstance(quota, (int, float)) and quota >= 0
        else NOT_AVAILABLE,
        "server": value_or_na(
            find_first(account_config, ALIASES["server"])
            or find_first(account_object, ALIASES["server"])
        ),
        "account_acl": account_acl if account_acl is not None else {},
        "account_acl_decoded": decode_account_acl(account_acl),
        "account_root_acl": account_root_acl if account_root_acl is not None else {},
        "account_root_acl_decoded": decode_mailbox_acl(account_root_acl),
        "namespaces": account_namespaces if account_namespaces is not None else [],
        "delegated_namespaces": delegated_namespaces(account_namespaces),
        "rules": account_rules,
        "rule_count": len(account_rules),
        "has_rules": bool(account_rules),
        "explicit_settings": explicit_settings,
        "has_settings": bool(explicit_settings),
        "is_admin": is_admin,
        "access_grants_out": [],
        "access_grants_in": [],
        "acl_grants_out_count": 0,
        "acl_grants_in_count": 0,
        "has_any_acl": False,
    }


def apply_imap_account_storage(accounts: List[Dict[str, Any]], records: List[Dict[str, Any]]) -> None:
    totals: Dict[Tuple[str, str], int] = defaultdict(int)
    for record in records:
        size = record.get("disk_size_used_bytes")
        if isinstance(size, int):
            totals[(str(record.get("domain")), str(record.get("account")))] += size
    for account in accounts:
        key = (str(account.get("domain")), str(account.get("account")))
        total = totals.get(key, 0)
        account["disk_size_used"] = human_size(total)
        account["disk_size_used_bytes"] = total
        account["disk_size_source"] = "imap_mailbox_sum"


def resolve_acl_principal(identifier: Any, owner_domain: str) -> Optional[Tuple[str, str]]:
    text = str(identifier or "").lstrip("+-")
    if text.startswith("-"):
        text = text[1:]
    if not text or text in ("owner", "anyone", "anyone@") or text.startswith("="):
        return None
    if "@" in text:
        account, domain = text.split("@", 1)
    else:
        account, domain = text, owner_domain
    if not account or not domain:
        return None
    return (domain.lower(), account.lower())


def add_access_edges(
    account_map: Dict[Tuple[str, str], Dict[str, Any]],
    owner_domain: str,
    owner_account: str,
    resource_type: str,
    resource_label: str,
    decoded_acl: Any,
) -> None:
    owner_key = (owner_domain.lower(), owner_account.lower())
    owner_summary = account_map.get(owner_key)
    if not owner_summary or not decoded_acl:
        return

    for item in decoded_acl:
        identifier = str(item.get("identifier", ""))
        out_entry = {
            "to": identifier,
            "resource_type": resource_type,
            "resource": resource_label,
            "effect": item.get("effect"),
            "rights": item.get("rights", {}),
        }
        owner_summary["access_grants_out"].append(out_entry)
        principal_key = resolve_acl_principal(identifier, owner_domain)
        if principal_key and principal_key in account_map:
            account_map[principal_key]["access_grants_in"].append(
                {
                    "from": f"{owner_account}@{owner_domain}",
                    "resource_type": resource_type,
                    "resource": resource_label,
                    "effect": item.get("effect"),
                    "rights": item.get("rights", {}),
                }
            )


def annotate_access(accounts: List[Dict[str, Any]], records: List[Dict[str, Any]]) -> None:
    account_map = {
        (str(account.get("domain", "")).lower(), str(account.get("account", "")).lower()): account
        for account in accounts
    }
    for account in accounts:
        domain = str(account.get("domain", ""))
        name = str(account.get("account", ""))
        add_access_edges(
            account_map,
            domain,
            name,
            "account",
            f"аккаунт {name}@{domain}",
            account.get("account_acl_decoded"),
        )
        add_access_edges(
            account_map,
            domain,
            name,
            "root_folders",
            f"корневые папки {name}@{domain}",
            account.get("account_root_acl_decoded"),
        )

    for record in records:
        if record.get("access_rights_source") != "folder":
            continue
        domain = str(record.get("domain", ""))
        account = str(record.get("account", ""))
        folder = str(record.get("folder_name", record.get("mailbox_name", "")))
        add_access_edges(
            account_map,
            domain,
            account,
            "folder",
            f"папка {folder} аккаунта {account}@{domain}",
            record.get("access_rights_decoded"),
        )

    for account in accounts:
        account["acl_grants_out_count"] = len(account.get("access_grants_out") or [])
        account["acl_grants_in_count"] = len(account.get("access_grants_in") or [])
        account["has_any_acl"] = bool(
            account["acl_grants_out_count"] or account["acl_grants_in_count"]
        )


def collect_domain_cmd(client: CmdClient, domain: str) -> Dict[str, Any]:
    errors: List[Dict[str, str]] = []
    rules: List[Dict[str, Any]] = []
    explicit_settings: Dict[str, Any] = {}
    try:
        rules = get_rules(client, domain, None)
    except Exception as e:
        errors.append({"domain": domain, "stage": "RulesList domain", "error": str(e)})
    domain_aliases = []
    try:
        dom_exp = normalize_mapping(client.send_json(f"DomainReadConfig {q(domain)} false"))
        domain_aliases = dom_exp.get("names", [])
        explicit_settings = {str(k): dom_exp[k] for k in dom_exp if str(k) not in DOMAIN_INTRINSIC_KEYS}
    except Exception as e:
        errors.append({"domain": domain, "stage": "DomainReadConfig", "error": str(e)})
    return {
        "domain": domain,
        "rules": rules,
        "rule_count": len(rules),
        "has_rules": bool(rules),
        "explicit_settings": explicit_settings,
        "has_settings": bool(explicit_settings),
        "domain_aliases": domain_aliases,
        "errors": errors,
    }


def collect_account_cmd(
    client: CmdClient,
    opts: argparse.Namespace,
    domain: str,
    account_uid: str,
    account_object: Any,
) -> Dict[str, Any]:
    account = account_name_from_object(account_uid, account_object)
    account_config = (
        get_account_config(client, domain, account)
        if opts.include_account_config
        else normalize_mapping(account_object)
    )
    if opts.account_storage_source == "imap" and opts.include_imap_stats:
        account_storage = None
    else:
        account_storage = get_account_storage_size(
            client, domain, account, opts.recalculate_storage
        )
    account_quota = find_first(account_config, ALIASES["quota"])
    if account_quota is None:
        account_quota = get_account_quota(client, domain, account)
    mailboxes = get_mailboxes(client, domain, account, opts.mailbox_class)
    account_acl = find_first(account_config, ["AccountACL", "account_acl"])
    account_rules: List[Dict[str, Any]] = []
    account_root_acl = None
    account_namespaces = []
    explicit_settings: Dict[str, Any] = {}
    is_admin = False
    errors: List[Dict[str, str]] = []
    account_names = []
    identities = []
    object_type = "Account"
    group_members = []
    forward_to = ""
    resource_type = ""
    capacity = ""
    owner_of = []

    try:
        acc_exp = normalize_mapping(client.send_json(f"OBJECTREADCONFIG {q(domain)} {q(account)} false"))
        
        object_type = get_object_type(account_object).capitalize()
        
        raw_names = acc_exp.get("names", [])
        account_names = []
        for name in raw_names:
            if isinstance(name, str):
                m = re.match(r"^/[A-Z]\s+(\d+)\s+(.*)$", name)
                if m:
                    account_names.append(f"{m.group(2)} ({m.group(1)})")
                else:
                    account_names.append(name)
            else:
                account_names.append(name)

        identities = acc_exp.get("Identities", [])
        group_members = acc_exp.get("Members", [])
        forward_to = acc_exp.get("ForwardTo", "")
        resource_type = acc_exp.get("ResourceType", "")
        capacity = acc_exp.get("Capacity", "")
        owner_of = acc_exp.get("Owner", acc_exp.get("OwnerOf", []))

        exclude_keys = ACCOUNT_INTRINSIC_KEYS | {"Identities", "Members", "ForwardTo", "ResourceType", "Capacity", "Owner", "OwnerOf"}
        explicit_settings = {str(k): acc_exp[k] for k in acc_exp if str(k) not in exclude_keys}

        if acc_exp.get("IsAdmin"):
            is_admin = True
        elif acc_exp.get("AdminIn") and acc_exp.get("AdminIn") != [None]:
            is_admin = True
    except Exception as e:
        errors.append(
            {
                "domain": domain,
                "account": account,
                "stage": "OBJECTREADCONFIG explicit",
                "error": str(e),
            }
        )
    try:
        account_rules = get_rules(client, domain, account)
    except Exception as e:
        errors.append(
            {
                "domain": domain,
                "account": account,
                "stage": "RulesList account",
                "error": str(e),
            }
        )
    if opts.include_acl:
        if account_acl is None:
            try:
                account_acl = get_account_acl(client, domain, account)
            except Exception as e:
                errors.append(
                    {
                        "domain": domain,
                        "account": account,
                        "stage": "AccountACL",
                        "error": str(e),
                    }
                )
        try:
            account_root_acl = get_mailbox_acl(client, domain, account, "")
        except Exception as e:
            errors.append(
                {
                    "domain": domain,
                    "account": account,
                    "stage": "MailboxGetACL account_root",
                    "error": str(e),
                }
            )
        try:
            account_namespaces = get_account_namespaces(client, domain, account)
        except Exception as e:
            errors.append(
                {
                    "domain": domain,
                    "account": account,
                    "stage": "AccountListNamespaces",
                    "error": str(e),
                }
            )
    return {
        "domain": domain,
        "account": account,
        "account_uid": str(account_uid),
        "account_object": account_object,
        "account_config": account_config,
        "account_storage": account_storage,
        "account_quota": account_quota,
        "account_acl": account_acl,
        "account_root_acl": account_root_acl,
        "account_namespaces": account_namespaces,
        "account_rules": account_rules,
        "mailboxes": mailboxes,
        "explicit_settings": explicit_settings,
        "is_admin": is_admin,
        "account_names": account_names,
        "identities": identities,
        "object_type": object_type,
        "group_members": group_members,
        "forward_to": forward_to,
        "resource_type": resource_type,
        "capacity": capacity,
        "owner_of": owner_of,
        "errors": errors,
    }


def collect_inventory(
    client: CmdClient,
    opts: argparse.Namespace,
    imap_pool: Optional[ImapStatsPool] = None,
    cmd_pool: Optional[CmdClientPool] = None,
) -> Dict[str, Any]:
    record_contexts: List[Dict[str, Any]] = []
    account_summaries: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    imap_futures: Dict[Any, int] = {}
    info_futures: Dict[Any, int] = {}
    acl_futures: Dict[Any, int] = {}
    domain_summaries: List[Dict[str, Any]] = []
    domains = get_domains(client, opts.domains, opts.all_domains)

    explicit_accounts = parse_accounts(opts.accounts)

    domain_items = sorted(domains.items(), key=lambda item: item[1])
    if opts.max_domains is not None:
        domain_items = domain_items[: int(opts.max_domains)]

    if cmd_pool is not None:
        domain_rule_futures = {
            cmd_pool.submit(collect_domain_cmd, domain): (uid, domain)
            for uid, domain in domain_items
        }
        print(f"Waiting for CMD domain rules: {len(domain_rule_futures)} domains", file=sys.stderr)
        for future in as_completed(domain_rule_futures):
            uid, domain = domain_rule_futures[future]
            try:
                result = future.result()
                errors.extend(result.pop("errors", []))
                result["domain_uid"] = uid
                domain_summaries.append(result)
            except Exception as e:
                errors.append({"domain": domain, "stage": "RulesList domain", "error": str(e)})
                domain_summaries.append({"domain": domain, "domain_uid": uid, "rules": [], "rule_count": 0, "has_rules": False})
    else:
        for uid, domain in domain_items:
            result = collect_domain_cmd(client, domain)
            errors.extend(result.pop("errors", []))
            result["domain_uid"] = uid
            domain_summaries.append(result)

    accounts_by_domain: Dict[str, List[Tuple[str, Any]]] = {}
    raw_domain_objects: Dict[str, Dict[Any, Any]] = {}
    if explicit_accounts:
        for _, domain in domain_items:
            print(f"Domain: {domain}", file=sys.stderr)
            accounts = {
                account: account
                for account_domain, account in explicit_accounts
                if account_domain is None or account_domain == domain
            }
            account_items = list(accounts.items())
            if opts.max_accounts_per_domain is not None:
                account_items = account_items[: int(opts.max_accounts_per_domain)]
            accounts_by_domain[domain] = [
                (str(account_uid), account_object)
                for account_uid, account_object in account_items
                if opts.include_non_mail_objects or object_is_mail_account(account_object)
            ]
    elif cmd_pool is not None:
        object_futures = {
            cmd_pool.submit(get_objects, domain, opts.page_size): domain
            for _, domain in domain_items
        }
        print(f"Waiting for CMD domain objects: {len(object_futures)} domains", file=sys.stderr)
        for future in as_completed(object_futures):
            domain = object_futures[future]
            print(f"Domain: {domain}", file=sys.stderr)
            try:
                accounts = future.result()
            except Exception as e:
                errors.append({"domain": domain, "stage": "OBJECTSLIST", "error": str(e)})
                continue
            raw_domain_objects[domain] = accounts
            account_items = list(accounts.items())
            if opts.max_accounts_per_domain is not None:
                account_items = account_items[: int(opts.max_accounts_per_domain)]
            accounts_by_domain[domain] = [
                (str(account_uid), account_object)
                for account_uid, account_object in account_items
                if opts.include_non_mail_objects or object_is_mail_account(account_object)
            ]
    else:
        for _, domain in domain_items:
            print(f"Domain: {domain}", file=sys.stderr)
            try:
                accounts = get_objects(client, domain, opts.page_size)
            except Exception as e:
                errors.append({"domain": domain, "stage": "OBJECTSLIST", "error": str(e)})
                continue
            raw_domain_objects[domain] = accounts
            account_items = list(accounts.items())
            if opts.max_accounts_per_domain is not None:
                account_items = account_items[: int(opts.max_accounts_per_domain)]
            accounts_by_domain[domain] = [
                (str(account_uid), account_object)
                for account_uid, account_object in account_items
                if opts.include_non_mail_objects or object_is_mail_account(account_object)
            ]

    global_id_map: Dict[str, str] = {}
    domain_to_uid = {name: uid for uid, name in domain_items}
    for domain, objects in raw_domain_objects.items():
        domain_uid = domain_to_uid.get(domain)
        if domain_uid:
            for obj_uid, obj in objects.items():
                name = account_name_from_object(str(obj_uid), obj)
                global_id_map[f"={obj_uid}@={domain_uid}"] = f"{name}@{domain}"

    account_results: List[Tuple[int, Dict[str, Any]]] = []
    account_futures: Dict[Any, Tuple[int, str, str]] = {}
    sequence = 0
    for domain, account_items in accounts_by_domain.items():
        for account_uid, account_object in account_items:
            account = account_name_from_object(account_uid, account_object)
            obj_type_label = get_object_type(account_object).capitalize()
            print(f"  {obj_type_label}: {account}", file=sys.stderr)
            if cmd_pool is not None:
                account_futures[
                    cmd_pool.submit(collect_account_cmd, opts, domain, account_uid, account_object)
                ] = (sequence, domain, account)
            else:
                try:
                    account_results.append(
                        (
                            sequence,
                            collect_account_cmd(client, opts, domain, account_uid, account_object),
                        )
                    )
                except Exception as e:
                    errors.append(
                        {
                            "domain": domain,
                            "account": account,
                            "stage": "account",
                            "error": str(e),
                        }
                    )
            sequence += 1

    if account_futures:
        print(f"Waiting for CMD object data: {len(account_futures)} objects", file=sys.stderr)
        for future in as_completed(account_futures):
            sequence, domain, account = account_futures[future]
            try:
                account_results.append((sequence, future.result()))
            except Exception as e:
                errors.append(
                    {
                        "domain": domain,
                        "account": account,
                        "stage": "account",
                        "error": str(e),
                    }
                )

    for _, account_data in sorted(account_results, key=lambda item: item[0]):
        domain = account_data["domain"]
        account = account_data["account"]
        account_uid = account_data["account_uid"]
        account_object = account_data["account_object"]
        account_config = account_data["account_config"]
        account_storage = account_data["account_storage"]
        account_quota = account_data["account_quota"]
        account_acl = account_data["account_acl"]
        account_root_acl = account_data["account_root_acl"]
        account_namespaces = account_data.get("account_namespaces", [])
        account_rules = account_data.get("account_rules", [])
        explicit_settings = account_data.get("explicit_settings", {})
        is_admin = account_data.get("is_admin", False)
        errors.extend(account_data.get("errors", []))
        mailbox_items = list(iter_named_items(account_data["mailboxes"]))
        if opts.max_mailboxes_per_account is not None:
            mailbox_items = mailbox_items[: int(opts.max_mailboxes_per_account)]

        summary = build_account_summary(
            domain=domain,
            account=account,
            account_uid=account_uid,
            account_object=account_object,
            account_config=account_config,
            account_storage=account_storage,
            account_quota=account_quota,
            mailbox_count=len(mailbox_items),
            account_root_acl=account_root_acl,
            account_acl=account_acl,
            account_namespaces=account_namespaces,
            account_rules=account_rules,
            explicit_settings=explicit_settings,
            is_admin=is_admin,
        )
        summary["account_names"] = account_data.get("account_names", [])
        summary["identities"] = account_data.get("identities", [])
        summary["object_type"] = account_data.get("object_type", "Account")
        summary["group_members"] = account_data.get("group_members", [])
        summary["forward_to"] = account_data.get("forward_to", "")
        summary["resource_type"] = account_data.get("resource_type", "")
        summary["capacity"] = account_data.get("capacity", "")
        summary["owner_of"] = account_data.get("owner_of", [])
        
        account_summaries.append(summary)

        for mailbox_name, mailbox_value in mailbox_items:
            if mailbox_name is None:
                errors.append(
                    {
                        "domain": domain,
                        "account": account,
                        "stage": "MAILBOXESLIST",
                        "error": f"Cannot determine mailbox name from {mailbox_value!r}",
                    }
                )
                continue

            context = {
                "domain": domain,
                "account": account,
                "account_uid": account_uid,
                "account_object": account_object,
                "account_config": account_config,
                "account_quota": account_quota,
                "account_root_acl": account_root_acl,
                "mailbox_name": mailbox_name,
                "mailbox_value": mailbox_value,
                "mailbox_info": {},
                "mailbox_stats": None,
                "acl": None,
            }
            index = len(record_contexts)
            record_contexts.append(context)
            if opts.include_mailbox_info:
                if cmd_pool is not None:
                    info_futures[cmd_pool.submit(get_mailbox_info, domain, account, mailbox_name)] = index
                else:
                    try:
                        context["mailbox_info"] = get_mailbox_info(
                            client, domain, account, mailbox_name
                        )
                    except Exception as e:
                        errors.append(
                            {
                                "domain": domain,
                                "account": account,
                                "mailbox": mailbox_name,
                                "stage": "MboxGetInfo",
                                "error": str(e),
                            }
                        )
            if opts.include_acl:
                if cmd_pool is not None:
                    acl_futures[cmd_pool.submit(get_mailbox_acl, domain, account, mailbox_name)] = index
                else:
                    try:
                        context["acl"] = get_mailbox_acl(client, domain, account, mailbox_name)
                    except Exception as e:
                        errors.append(
                            {
                                "domain": domain,
                                "account": account,
                                "mailbox": mailbox_name,
                                "stage": "MailboxGetACL",
                                "error": str(e),
                            }
                        )
            if imap_pool is not None:
                imap_futures[imap_pool.submit(domain, account, mailbox_name)] = index

    if info_futures:
        print(f"Waiting for CMD folder info: {len(info_futures)} folders", file=sys.stderr)
        for future in as_completed(info_futures):
            index = info_futures[future]
            context = record_contexts[index]
            try:
                context["mailbox_info"] = future.result()
            except Exception as e:
                errors.append(
                    {
                        "domain": context["domain"],
                        "account": context["account"],
                        "mailbox": context["mailbox_name"],
                        "stage": "MboxGetInfo",
                        "error": str(e),
                    }
                )

    if acl_futures:
        print(f"Waiting for CMD ACL: {len(acl_futures)} folders", file=sys.stderr)
        for future in as_completed(acl_futures):
            index = acl_futures[future]
            context = record_contexts[index]
            try:
                context["acl"] = future.result()
            except Exception as e:
                errors.append(
                    {
                        "domain": context["domain"],
                        "account": context["account"],
                        "mailbox": context["mailbox_name"],
                        "stage": "MailboxGetACL",
                        "error": str(e),
                    }
                )

    if imap_futures:
        print(f"Waiting for IMAP stats: {len(imap_futures)} folders", file=sys.stderr)
        for future in as_completed(imap_futures):
            index = imap_futures[future]
            context = record_contexts[index]
            try:
                context["mailbox_stats"] = future.result()
            except Exception as e:
                errors.append(
                    {
                        "domain": context["domain"],
                        "account": context["account"],
                        "mailbox": context["mailbox_name"],
                        "stage": "IMAP_STATS",
                        "error": str(e),
                    }
                )

    records = [
        build_mailbox_record(
            domain=context["domain"],
            account=context["account"],
            account_uid=context["account_uid"],
            account_object=context["account_object"],
            account_config=context["account_config"],
            account_quota=context["account_quota"],
            mailbox_name=context["mailbox_name"],
            mailbox_list_value=context["mailbox_value"],
            mailbox_info=context["mailbox_info"],
            mailbox_stats=context["mailbox_stats"],
            acl=context["acl"],
            account_root_acl=context["account_root_acl"],
            include_raw=opts.include_raw,
        )
        for context in record_contexts
    ]
    if opts.account_storage_source == "imap" and imap_pool is not None:
        apply_imap_account_storage(account_summaries, records)
    annotate_access(account_summaries, records)

    return {
        "domains": domain_summaries,
        "accounts": account_summaries,
        "folders": records,
        "mailboxes": records,
        "errors": errors,
        "global_id_map": global_id_map,
    }


def parse_accounts(values: List[str]) -> List[Tuple[Optional[str], str]]:
    result: List[Tuple[Optional[str], str]] = []
    for value in values or []:
        if "@" in value:
            account, domain = value.split("@", 1)
            result.append((domain, account))
        else:
            result.append((None, value))
    return result


def write_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_csv(path: str, records: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "folder_uid",
        "folder_name",
        "mailbox_uid",
        "mailbox_name",
        "created_at",
        "owner",
        "access_rights",
        "access_rights_decoded",
        "message_count",
        "disk_quota_max",
        "disk_quota_max_bytes",
        "disk_size_used",
        "disk_size_used_bytes",
        "server",
        "domain",
        "account",
        "account_uid",
    ]

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["access_rights"] = json.dumps(
                row.get("access_rights"), ensure_ascii=False, separators=(",", ":")
            )
            row["access_rights_decoded"] = json.dumps(
                row.get("access_rights_decoded"), ensure_ascii=False, separators=(",", ":")
            )
            writer.writerow(row)


def html_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif value is None:
        text = ""
    else:
        text = str(value)
    return html.escape(text)


def sort_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(value).rjust(20, "0")
    if value is None:
        return ""
    return str(value).lower()


def number_value(value: Any) -> int:
    return value if isinstance(value, int) else 0


def date_cell(value: Any) -> str:
    if not non_empty(value) or value == NOT_AVAILABLE:
        return html_cell(value)
    escaped = html.escape(str(value))
    return f'<time datetime="{escaped}" data-utc="{escaped}">{escaped}</time>'


def acl_decoded_html(
    decoded: Any,
    empty_label: str = "No explicit ACL",
    resource_label: str = "объект",
) -> str:
    if not decoded:
        return f'<span class="empty">{html.escape(empty_label)}</span>'
    rows = []
    for item in decoded:
        identifier = html.escape(str(item.get("identifier", "")))
        effect = item.get("effect")
        resource = html.escape(resource_label)
        note = item.get("identifier_note")
        note_html = f' <span class="muted">({html.escape(str(note))})</span>' if note else ""
        rights = item.get("rights", {})
        raw = html.escape(str(rights.get("raw", "")))
        aliases = rights.get("aliases") or {}
        alias_text = ""
        if aliases:
            alias_text = " aliases: " + ", ".join(
                f"{key}={value}" for key, value in sorted(aliases.items())
            )
        summary_categories = [
            RIGHT_CATEGORY_LABELS.get(key, key)
            for key, values in (rights.get("categories") or {}).items()
            if values
        ]
        summary = ", ".join(summary_categories) if summary_categories else "нет известных прав"
        parts = []
        for category, values in (rights.get("categories") or {}).items():
            label = RIGHT_CATEGORY_LABELS.get(category, category)
            rendered = ", ".join(
                f'<code>{html.escape(str(value.get("code", "")))}</code> {html.escape(str(value.get("description", "")))}'
                for value in values
            )
            parts.append(f"<li><b>{html.escape(label)}</b>: {rendered}</li>")
        if effect == "subtract":
            action = f"у <b>{identifier}</b>{note_html} отнимается доступ к {resource}"
        else:
            action = f"<b>{identifier}</b>{note_html} получает доступ к {resource}"
        rows.append(
            '<details class="acl-entry">'
            f'<summary>{action}: {html.escape(summary)} '
            f'<span class="raw-rights">raw: {raw}{html.escape(alias_text)}</span></summary>'
            f'<ul>{"".join(parts)}</ul>'
            '</details>'
        )
    return f'<div class="acl-list">{"".join(rows)}</div>'


def access_rights_cell(record: Dict[str, Any]) -> str:
    rights = record.get("access_rights")
    folder_name = record.get("folder_name", record.get("mailbox_name", "папку"))
    account = record.get("account", "")
    domain = record.get("domain", "")
    resource = f'папке "{folder_name}" аккаунта {account}@{domain}'
    text = acl_decoded_html(record.get("access_rights_decoded"), resource_label=resource)
    source = record.get("access_rights_source")
    if source and source != "none" and not acl_is_empty(rights):
        source_label = {
            "folder": "ACL папки",
            "account_root": "корневой ACL папок",
        }.get(str(source), str(source))
        text += f'<span class="tag">{html.escape(source_label)}</span>'
    return text


def rights_summary_text(rights: Any) -> str:
    categories = [
        RIGHT_CATEGORY_LABELS.get(key, key)
        for key, values in (rights or {}).get("categories", {}).items()
        if values
    ]
    raw = (rights or {}).get("raw", "")
    if categories and raw:
        return f"{', '.join(categories)} (raw: {raw})"
    return ", ".join(categories) or (f"raw: {raw}" if raw else "")


def access_edges_html(edges: Any, title: str, empty_label: str) -> str:
    if not edges:
        return ""
    items = []
    for edge in edges:
        counterparty = edge.get("to") or edge.get("from") or ""
        resource = edge.get("resource", "")
        rights = rights_summary_text(edge.get("rights"))
        items.append(
            "<li>"
            f"<b>{html.escape(str(counterparty))}</b>: "
            f"{html.escape(str(resource))}"
            f"{' - ' + html.escape(rights) if rights else ''}"
            "</li>"
        )
    if not items:
        return f"<b>{html.escape(title)}</b>: <span class=\"empty\">{html.escape(empty_label)}</span>"
    return f"<b>{html.escape(title)}</b><ul class=\"compact-list\">{''.join(items)}</ul>"


def rule_summary(rule: Dict[str, Any]) -> str:
    parts = []
    if "priority" in rule:
        parts.append(f"priority {rule.get('priority')}")
    if rule.get("autoReply"):
        parts.append("auto reply")
    if isinstance(rule.get("conditions"), list):
        parts.append(f"conditions {len(rule.get('conditions') or [])}")
    if isinstance(rule.get("actions"), list):
        parts.append(f"actions {len(rule.get('actions') or [])}")
    return ", ".join(parts)


def format_rule_items(items: Any) -> str:
    if not isinstance(items, list):
        return html.escape(str(items))
    rows = []
    for item in items:
        if isinstance(item, list):
            parts = [f"<span style='display:inline-block; margin-right:24px;'>{html.escape(str(p))}</span>" for p in item]
            rows.append(f"<div style='padding-left:8px; margin:2px 0;'>{''.join(parts)}</div>")
        else:
            rows.append(f"<div style='padding-left:8px; margin:2px 0;'>{html.escape(str(item))}</div>")
    return "".join(rows)


def rules_html(rules: Any, title: str, id_map: Optional[Dict[str, str]] = None) -> str:
    if not rules:
        return ""
    items = []
    for rule in rules:
        if not isinstance(rule, dict):
            items.append(f"<li>{html_cell(rule)}</li>")
            continue
        name = rule.get("name", NOT_AVAILABLE)
        summary = rule_summary(rule)
        details = []
        if rule.get("conditions"):
            details.append(f"<div style='margin-top:4px;'><b>Условия:</b>{format_rule_items(rule.get('conditions'))}</div>")
        if rule.get("actions"):
            details.append(f"<div style='margin-top:4px;'><b>Действия:</b>{format_rule_items(rule.get('actions'))}</div>")
        body = "".join(details)
        if body:
             items.append(f"<li><details class='inline-setting'><summary><b>{html.escape(str(name))}</b>{': ' + html.escape(summary) if summary else ''}</summary>{body}</details></li>")
        else:
             items.append(f"<li><b>{html.escape(str(name))}</b>{': ' + html.escape(summary) if summary else ''}</li>")
    return f"<b>{html.escape(title)}</b><ul class=\"compact-list\">{''.join(items)}</ul>"


ID_PATTERN = re.compile(r"^=(\d+)(@=(\d+))?$")
TIME_PATTERN = re.compile(r"^/T (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)$")

def try_render_table(d: Any, id_map: Optional[Dict[str, str]]) -> Optional[str]:
    items_to_render = []
    if isinstance(d, list) and d and all(isinstance(x, dict) for x in d):
        items_to_render = [(None, x) for x in d]
    elif isinstance(d, dict) and d and all(isinstance(x, dict) for x in d.values()):
        items_to_render = list(d.items())
    else:
        return None

    all_keys = []
    for _, item in items_to_render:
        for k in item.keys():
            k_str = str(k)
            if k_str not in all_keys:
                all_keys.append(k_str)
    
    if not all_keys or len(all_keys) > 8:
        return None
        
    has_keys = any(k is not None for k, _ in items_to_render)
    
    rows = []
    header_cells = []
    if has_keys:
        header_cells.append("<th>#</th>")
    for k in all_keys:
        header_cells.append(f"<th>{html.escape(k)}</th>")
    
    rows.append("<thead><tr>" + "".join(header_cells) + "</tr></thead>")
    rows.append("<tbody>")
    
    for key, item in items_to_render:
        cells = []
        if has_keys:
            cells.append(f"<td><b>{html.escape(str(key))}</b></td>")
        for k in all_keys:
            cells.append(f"<td>{dict_to_html_list(item.get(k), id_map)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    
    rows.append("</tbody>")
    return f"<div style='max-width:100%; overflow-x:auto; margin:4px 0 6px 4px;'><table class='settings-table'>{''.join(rows)}</table></div>"

def dict_to_html_list(d: Any, id_map: Optional[Dict[str, str]] = None) -> str:
    table_html = try_render_table(d, id_map)
    if table_html:
        return table_html

    if isinstance(d, dict):
        if not d:
            return "<span class=\"empty\">пусто</span>"
        items = []
        for k, v in d.items():
            if isinstance(v, (dict, list)) and len(v) > 3:
                items.append(f"<li><details class='inline-setting'><summary><b>{html.escape(str(k))}</b></summary>{dict_to_html_list(v, id_map)}</details></li>")
            else:
                items.append(f"<li><b>{html.escape(str(k))}</b>: {dict_to_html_list(v, id_map)}</li>")
        return f'<ul class="compact-list" style="margin-top:2px;">{"".join(items)}</ul>'
    elif isinstance(d, list):
        if not d:
            return "<span class=\"empty\">пусто</span>"
        items = []
        for item in d:
            items.append(f"<li>{dict_to_html_list(item, id_map)}</li>")
        return f'<ul class="compact-list" style="margin-top:2px;">{"".join(items)}</ul>'
    elif d is None:
        return "<span class=\"empty\">пусто</span>"
    else:
        text = str(d)
        if text in ("9223372036854775807", "2147483647"):
            return "Никогда"
        
        time_match = TIME_PATTERN.match(text)
        if time_match:
            iso_time = time_match.group(1)
            escaped = html.escape(iso_time)
            return f'<time datetime="{escaped}" data-utc="{escaped}">{escaped}</time>'
            
        if id_map and ID_PATTERN.match(text):
            resolved = id_map.get(text)
            if resolved:
                return f"{html.escape(text)} ({html.escape(resolved)})"
        return html.escape(text)


def settings_html(settings: Dict[str, Any], title: str = "Дополнительные настройки", id_map: Optional[Dict[str, str]] = None) -> str:
    if not settings:
        return ""
    raw_json = html.escape(json.dumps(settings, ensure_ascii=False, indent=2))
    raw_block = f"<details class='inline-setting' style='margin-top:8px;'><summary style='font-size:11px; color:var(--muted);'>Исходный JSON</summary><pre class='settings-block'>{raw_json}</pre></details>"
    return f"<b>{html.escape(title)}</b><br>{dict_to_html_list(settings, id_map)}{raw_block}"


def write_html(path: str, data: Dict[str, Any], title: str) -> None:
    records = data.get("folders", data.get("mailboxes", []))
    accounts = data.get("accounts", [])
    errors = data.get("errors", [])
    domain_summary_items = data.get("domains", [])
    domains: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        domains[str(record.get("domain", NOT_AVAILABLE))][str(record.get("account", NOT_AVAILABLE))].append(record)
    account_summaries = {
        (str(item.get("domain", NOT_AVAILABLE)), str(item.get("account", NOT_AVAILABLE))): item
        for item in accounts
        if isinstance(item, dict)
    }
    for domain, account in account_summaries:
        domains[domain][account]
    domain_summaries = {
        str(item.get("domain", NOT_AVAILABLE)): item
        for item in domain_summary_items
        if isinstance(item, dict)
    }

    id_map: Dict[str, str] = data.get("global_id_map", {})
    for domain, summary in domain_summaries.items():
        if summary.get("domain_uid"):
            id_map[f"={summary['domain_uid']}"] = domain
            id_map[str(summary['domain_uid'])] = domain
    for (domain, account), summary in account_summaries.items():
        domain_uid = domain_summaries.get(domain, {}).get("domain_uid")
        if summary.get("account_uid") and domain_uid:
            id_map[f"={summary['account_uid']}@={domain_uid}"] = f"{account}@{domain}"

    rows: List[str] = []
    for domain in sorted(domains):
        account_count = len(domains[domain])
        folder_count = sum(len(items) for items in domains[domain].values())
        domain_size = sum(
            number_value(account_summaries.get((domain, account), {}).get("disk_size_used_bytes"))
            for account in domains[domain]
        )
        domain_size_label = human_size(domain_size)
        domain_summary = domain_summaries.get(domain, {})
        domain_rules = domain_summary.get("rules") or []
        domain_explicit_settings = domain_summary.get("explicit_settings") or {}
        domain_has_special = bool(domain_rules or domain_explicit_settings)
        domain_badges = []
        if domain_rules:
            domain_badges.append(f"правил: {len(domain_rules)}")
        if domain_explicit_settings:
            domain_badges.append(f"настроек: {len(domain_explicit_settings)}")
        domain_badge_text = f", {', '.join(domain_badges)}" if domain_badges else ""
        rows.append(
            f'<details class="domain" open data-domain-name="{html.escape(domain.lower())}" '
            f'data-domain-size="{domain_size}" data-has-special="{str(domain_has_special).lower()}" '
            f'data-has-rules="{str(bool(domain_rules)).lower()}" data-has-settings="{str(bool(domain_explicit_settings)).lower()}"><summary>'
            f'<span class="summary-title"><span class="chevron"></span>{html.escape(domain)}</span>'
            f'<small>аккаунтов: {account_count}, папок: {folder_count}, занято {html.escape(domain_size_label)}{html.escape(domain_badge_text)}</small>'
            f'</summary><div class="domain-body">'
        )
        domain_rules_block = rules_html(domain_rules, "Правила домена", id_map)
        if domain_rules_block or domain_explicit_settings:
            meta_items = []
            if domain_explicit_settings:
                meta_items.append(settings_html(domain_explicit_settings, id_map=id_map))
            if domain_rules_block:
                meta_items.append(domain_rules_block)
            rows.append(f'<div class="account-meta domain-meta">{"<br>".join(meta_items)}</div>')
        for account in sorted(domains[domain]):
            account_rows = sorted(
                domains[domain][account],
                key=lambda x: str(x.get("folder_name", x.get("mailbox_name", ""))),
            )
            account_summary = account_summaries.get((domain, account), {})
            account_size = account_summary.get("disk_size_used", NOT_AVAILABLE)
            account_size_bytes = number_value(account_summary.get("disk_size_used_bytes"))
            if account_size == NOT_AVAILABLE:
                account_size = next(
                    (r.get("account_disk_size_used", NOT_AVAILABLE) for r in account_rows),
                    NOT_AVAILABLE,
                )
                account_size_bytes = number_value(
                    next(
                        (r.get("account_disk_size_used_bytes", 0) for r in account_rows),
                        0,
                    )
                )
            quota = account_summary.get("disk_quota_max", NOT_AVAILABLE)
            if quota == NOT_AVAILABLE:
                quota = next((r.get("disk_quota_max", NOT_AVAILABLE) for r in account_rows), NOT_AVAILABLE)
            account_acl = account_summary.get("account_acl")
            account_root_acl = account_summary.get("account_root_acl")
            namespaces = account_summary.get("delegated_namespaces") or []
            account_rules = account_summary.get("rules") or []
            explicit_settings = account_summary.get("explicit_settings") or {}
            is_admin = account_summary.get("is_admin", False)
            
            account_names = account_summary.get("account_names") or []
            identities = account_summary.get("identities") or []
            object_type = account_summary.get("object_type", "Account")
            group_members = account_summary.get("group_members") or []
            forward_to = account_summary.get("forward_to", "")
            resource_type = account_summary.get("resource_type", "")
            capacity = account_summary.get("capacity", "")
            owner_of = account_summary.get("owner_of") or []
            
            grants_out = account_summary.get("access_grants_out") or []
            grants_in = account_summary.get("access_grants_in") or []
            has_acl = bool(account_summary.get("has_any_acl"))
            has_special = bool(has_acl or account_rules or explicit_settings or is_admin)
            account_badges = []
            if is_admin:
                account_badges.append("admin")
            if not acl_is_empty(account_acl):
                account_badges.append("ACL аккаунта")
            if not acl_is_empty(account_root_acl):
                account_badges.append("корневой ACL папок")
            if grants_out:
                account_badges.append(f"выдает права: {len(grants_out)}")
            if grants_in:
                account_badges.append(f"получает права: {len(grants_in)}")
            if account_rules:
                account_badges.append(f"правил: {len(account_rules)}")
            if namespaces:
                account_badges.append(f"делегированных пространств: {len(namespaces)}")
            if explicit_settings:
                account_badges.append(f"настроек: {len(explicit_settings)}")
            badge_text = f", {', '.join(account_badges)}" if account_badges else ""
            
            type_icons = {"Account": "👤", "Group": "👥", "Resource": "🏢", "Forwarder": "↪️"}
            icon = type_icons.get(object_type, "👤")
            
            rows.append(
                f'<details class="account" data-account-name="{html.escape(account.lower())}" '
                f'data-account-size="{account_size_bytes}" data-has-special="{str(has_special).lower()}" '
                f'data-has-acl="{str(has_acl).lower()}" data-has-rules="{str(bool(account_rules)).lower()}" '
                f'data-has-settings="{str(bool(explicit_settings)).lower()}" '
                f'data-object-type="{html.escape(object_type.lower())}" '
                f'data-is-admin="{str(is_admin).lower()}"><summary>'
                f'<span class="summary-title"><span class="chevron"></span>{icon} {html.escape(account)} <small style="font-weight:normal; color:var(--muted)">({html.escape(object_type)})</small></span>'
                f'<small>папок: {len(account_rows)}, занято {html.escape(str(account_size))}, квота {html.escape(str(quota))}{html.escape(badge_text)}</small>'
                f'</summary>'
            )
            if (
                not acl_is_empty(account_acl)
                or not acl_is_empty(account_root_acl)
                or namespaces
                or grants_out
                or grants_in
                or account_rules
                or explicit_settings
                or account_names
                or identities
                or group_members
                or forward_to
                or resource_type
                or capacity
                or owner_of
            ):
                meta_items = []
                if account_names:
                    meta_items.append(f"<b>Имена аккаунта</b><br>{dict_to_html_list(account_names, id_map)}")
                if identities:
                    meta_items.append(f"<b>Идентификаторы (Identities)</b><br>{dict_to_html_list(identities, id_map)}")
                if forward_to:
                    meta_items.append(f"<b>Переадресация (ForwardTo)</b><br><span style='font-size:1.1em; color:var(--primary); font-weight:600;'>{html.escape(forward_to)}</span>")
                if group_members:
                    meta_items.append(f"<b>Участники группы ({len(group_members)})</b><br>{dict_to_html_list(group_members, id_map)}")
                if resource_type or capacity or owner_of:
                    res_info = []
                    if resource_type: res_info.append(f"<b>Тип ресурса:</b> {html.escape(str(resource_type))}")
                    if capacity: res_info.append(f"<b>Вместимость:</b> {html.escape(str(capacity))}")
                    if owner_of: res_info.append(f"<b>Владелец:</b><br>{dict_to_html_list(owner_of, id_map)}")
                    meta_items.append(f"<b>Свойства ресурса</b><br><div style='margin-left:10px; margin-top:4px;'>{'<br>'.join(res_info)}</div>")
                    
                if not acl_is_empty(account_acl):
                    meta_items.append(
                        "<b>ACL аккаунта</b>: "
                        + acl_decoded_html(
                            account_summary.get("account_acl_decoded"),
                            resource_label=f"аккаунту {account}@{domain}",
                        )
                    )
                if not acl_is_empty(account_root_acl):
                    meta_items.append(
                        "<b>Корневой ACL папок</b>: "
                        + acl_decoded_html(
                            account_summary.get("account_root_acl_decoded"),
                            resource_label=f"корневым папкам аккаунта {account}@{domain}",
                        )
                    )
                if namespaces:
                    meta_items.append(f"<b>Делегированные пространства</b>: {html_cell(namespaces)}")
                out_block = access_edges_html(grants_out, "Кому выданы права из этого аккаунта", "")
                if out_block:
                    meta_items.append(out_block)
                in_block = access_edges_html(grants_in, "Где этот аккаунт получает права", "")
                if in_block:
                    meta_items.append(in_block)
                rules_block = rules_html(account_rules, "Правила аккаунта", id_map)
                if rules_block:
                    meta_items.append(rules_block)
                if explicit_settings:
                    meta_items.append(settings_html(explicit_settings, id_map=id_map))
                rows.append(f'<div class="account-meta">{"<br>".join(meta_items)}</div>')
            rows.append(
                '<table class="sortable"><thead><tr>'
                '<th>Папка</th><th>UID</th><th>Создана</th><th>Владелец</th>'
                '<th>ACL</th><th>Сообщений</th><th>Размер папки</th><th>Квота</th>'
                '</tr></thead><tbody>'
            )
            if not account_rows:
                rows.append(
                    '<tr><td colspan="8" class="empty">У этого аккаунта нет папок</td></tr>'
                )
            for record in account_rows:
                rows.append(
                    "<tr>"
                    f'<td data-sort="{html.escape(sort_value(record.get("folder_name", record.get("mailbox_name"))))}">{html_cell(record.get("folder_name", record.get("mailbox_name")))}</td>'
                    f'<td data-sort="{html.escape(sort_value(record.get("folder_uid", record.get("mailbox_uid"))))}">{html_cell(record.get("folder_uid", record.get("mailbox_uid")))}</td>'
                    f'<td data-sort="{html.escape(sort_value(record.get("created_at")))}">{date_cell(record.get("created_at"))}</td>'
                    f'<td data-sort="{html.escape(sort_value(record.get("owner")))}">{dict_to_html_list(record.get("owner"), id_map)}</td>'
                    f'<td data-sort="{html.escape(sort_value(record.get("access_rights")))}">{access_rights_cell(record)}</td>'
                    f'<td data-sort="{html.escape(sort_value(record.get("message_count")))}">{html_cell(record.get("message_count"))}</td>'
                    f'<td data-sort="{html.escape(sort_value(record.get("disk_size_used_bytes")))}">{html_cell(record.get("disk_size_used"))}</td>'
                    f'<td data-sort="{html.escape(sort_value(record.get("disk_quota_max_bytes")))}">{html_cell(record.get("disk_quota_max"))}</td>'
                    "</tr>"
                )
            rows.append("</tbody></table></details>")
        rows.append("</div></details>")

    errors_html = ""
    if errors:
        error_items = "".join(
            f"<li>{html.escape(json.dumps(error, ensure_ascii=False))}</li>" for error in errors
        )
        errors_html = f'<details class="errors"><summary>Ошибки: {len(errors)}</summary><ul>{error_items}</ul></details>'

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --border:#d8dee4;
      --muted:#57606a;
      --bg:#f6f8fa;
      --panel:#ffffff;
      --text:#24292f;
      --accent:#0969da;
      --accent-bg:#ddf4ff;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Arial, sans-serif; color:var(--text); background:#ffffff; }}
    header {{ padding:24px 32px; background:var(--bg); border-bottom:1px solid var(--border); }}
    h1 {{ margin:0 0 10px; font-size:24px; }}
    .summary {{ display:flex; gap:16px; flex-wrap:wrap; color:var(--muted); }}
    main {{ padding:20px 32px 32px; }}
    .toolbar {{
      display:flex; gap:12px; flex-wrap:wrap; align-items:end;
      padding:14px; margin:0 0 16px; border:1px solid var(--border); border-radius:8px; background:var(--panel);
    }}
    .control {{ display:flex; flex-direction:column; gap:5px; min-width:190px; }}
    .control label {{ color:var(--muted); font-size:12px; }}
    input, select, button {{
      min-height:34px; border:1px solid var(--border); border-radius:6px; background:white;
      padding:6px 10px; font:inherit; color:var(--text);
    }}
    button {{ cursor:pointer; }}
    button:hover, select:hover, input:focus, select:focus {{ border-color:var(--accent); outline:none; }}
    details.domain, details.account, details.errors {{ border:1px solid var(--border); border-radius:8px; margin:0 0 12px; background:var(--panel); overflow:hidden; }}
    details.domain > summary, details.account > summary, details.errors > summary {{
      cursor:pointer; padding:12px 14px; display:flex; justify-content:space-between;
      gap:16px; align-items:center; list-style:none;
    }}
    details.domain > summary::-webkit-details-marker, details.account > summary::-webkit-details-marker, details.errors > summary::-webkit-details-marker {{ display:none; }}
    details.inline-setting {{ margin: 2px 0 2px 0px; }}
    details.inline-setting > summary {{ cursor: pointer; color: var(--text); }}
    .summary-title {{ display:flex; align-items:center; gap:8px; font-weight:600; }}
    .chevron {{
      width:18px; height:18px; border:1px solid var(--border); border-radius:4px;
      display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto; background:var(--bg);
    }}
    .chevron::before {{ content:"+"; color:var(--accent); font-weight:700; line-height:1; }}
    details[open] > summary .chevron::before {{ content:"-"; }}
    summary small {{ color:var(--muted); }}
    .domain > summary {{ background:#f6f8fa; }}
    .domain-body {{ padding:12px 12px 0; border-top:1px solid var(--border); background:#fbfbfc; }}
    .account {{ margin:0 0 12px; }}
    .account > summary {{ background:white; }}
    .account[data-has-special="true"] > summary {{ border-left:4px solid var(--accent); }}
    .account[open] > summary {{ background:var(--accent-bg); }}
    .account-meta {{ padding:10px 14px; border-top:1px solid var(--border); background:#fbfbfc; color:var(--muted); font-size:13px; }}
    .domain-meta {{ margin:0 0 12px; border:1px solid var(--border); border-radius:6px; }}
    .tag {{ display:inline-block; margin-left:6px; padding:1px 5px; border:1px solid var(--border); border-radius:999px; color:var(--muted); font-size:11px; white-space:nowrap; }}
    .compact-list {{ margin:6px 0 0 18px; padding:0; }}
    .compact-list li {{ margin:3px 0; }}
    .muted {{ color:var(--muted); }}
    .raw-rights {{ color:var(--muted); font-size:11px; margin-left:6px; }}
    .acl-list {{ display:grid; gap:4px; min-width:260px; }}
    .acl-entry {{ border:0; border-radius:0; margin:0; background:transparent; overflow:visible; }}
    .acl-entry > summary {{ display:block; padding:0; cursor:pointer; }}
    .acl-entry > ul {{ margin:4px 0 0 16px; padding:0; color:var(--muted); }}
    .acl-entry li {{ margin:2px 0; }}
    code {{ padding:1px 4px; border:1px solid var(--border); border-radius:4px; background:var(--bg); }}
    table {{ width:100%; border-collapse:collapse; border-top:1px solid var(--border); table-layout:auto; }}
    th, td {{ padding:8px 10px; border-bottom:1px solid var(--border); text-align:left; vertical-align:top; font-size:13px; }}
    th {{ background:var(--bg); cursor:pointer; user-select:none; white-space:nowrap; position:sticky; top:0; }}
    th::after {{ content:""; margin-left:6px; color:var(--accent); }}
    th[data-dir="asc"]::after {{ content:"^"; }}
    th[data-dir="desc"]::after {{ content:"v"; }}
    td {{ overflow-wrap:anywhere; }}
    tr:hover td {{ background:#f6f8fa; }}
    .errors summary {{ color:#cf222e; }}
    .empty {{ color:var(--muted); font-style:italic; }}
    .hidden {{ display:none !important; }}
    .settings-block {{ margin:4px 0 0; padding:8px; background:var(--bg); border:1px solid var(--border); border-radius:6px; white-space:pre-wrap; word-break:break-all; font-family:monospace; font-size:12px; }}
    table.settings-table {{ width:max-content; border-collapse:collapse; margin:0; border:1px solid var(--border); border-radius:6px; font-size:12px; overflow:hidden; }}
    table.settings-table th, table.settings-table td {{ border:1px solid var(--border); padding:3px 8px; text-align:left; overflow-wrap:normal; word-break:normal; }}
    table.settings-table th {{ background:var(--bg); font-weight:600; cursor:default; }}
    table.settings-table th::after {{ content:none; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="summary">
      <span>Домены: {len(domains)}</span>
      <span>Аккаунты: {sum(len(accounts) for accounts in domains.values())}</span>
      <span>Папки: {len(records)}</span>
      <span>Ошибки: {len(errors)}</span>
    </div>
  </header>
  <main>
    <section class="toolbar" aria-label="Report controls">
      <div class="control">
        <label for="accountFilter">Поиск по префиксу аккаунта</label>
        <input id="accountFilter" type="search" placeholder="например ivan">
      </div>
      <fieldset class="control" style="border:none; padding:0; margin:0; display:flex; flex-direction:column; gap:5px;">
        <label style="font-weight:600; margin-bottom:2px;">Типы объектов</label>
        <label><input id="typeAccount" type="checkbox" checked> Аккаунты</label>
        <label><input id="typeGroup" type="checkbox" checked> Группы</label>
        <label><input id="typeResource" type="checkbox" checked> Ресурсы</label>
        <label><input id="typeForwarder" type="checkbox" checked> Переадресаторы</label>
      </fieldset>
      <fieldset class="control" style="border:none; padding:0; margin:0; display:flex; flex-direction:column; gap:5px;">
        <label><input id="aclFilter" type="checkbox"> Только с ACL</label>
        <label><input id="rulesFilter" type="checkbox"> Только с правилами</label>
        <label><input id="settingsFilter" type="checkbox"> Только с измененными настройками</label>
        <label><input id="adminFilter" type="checkbox"> Только администраторы</label>
      </fieldset>
      <div class="control">
        <label for="domainSort">Сортировка доменов</label>
        <select id="domainSort">
          <option value="name-asc">По имени (A-Z)</option>
          <option value="name-desc">По имени (Z-A)</option>
          <option value="size-desc">По размеру (убывание)</option>
          <option value="size-asc">По размеру (возрастание)</option>
        </select>
      </div>
      <div class="control">
        <label for="accountSort">Сортировка аккаунтов</label>
        <select id="accountSort">
          <option value="name-asc">По имени (A-Z)</option>
          <option value="name-desc">По имени (Z-A)</option>
          <option value="size-desc">По размеру (убывание)</option>
          <option value="size-asc">По размеру (возрастание)</option>
        </select>
      </div>
      <div class="control" style="flex-direction:row; align-items:end; gap:8px;">
        <button type="button" id="expandAll">Развернуть все</button>
        <button type="button" id="collapseAll">Свернуть все</button>
      </div>
    </section>
    {errors_html}
    <section id="domainList">
      {''.join(rows)}
    </section>
  </main>
  <script>
    const collator = new Intl.Collator(undefined, {{ numeric: true, sensitivity: 'base' }});

    document.querySelectorAll('time[data-utc]').forEach((node) => {{
      const date = new Date(node.dataset.utc);
      if (!Number.isNaN(date.valueOf())) {{
        node.textContent = new Intl.DateTimeFormat(undefined, {{
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          timeZoneName: 'short'
        }}).format(date);
      }}
    }});

    function compareDetails(a, b, mode, nameKey, sizeKey) {{
      if (mode === 'size-asc' || mode === 'size-desc') {{
        const av = Number(a.dataset[sizeKey] || 0);
        const bv = Number(b.dataset[sizeKey] || 0);
        return mode === 'size-asc' ? av - bv : bv - av;
      }}
      const av = a.dataset[nameKey] || '';
      const bv = b.dataset[nameKey] || '';
      return mode === 'name-desc' ? collator.compare(bv, av) : collator.compare(av, bv);
    }}

    function sortDomains() {{
      const list = document.getElementById('domainList');
      const mode = document.getElementById('domainSort').value;
      Array.from(list.querySelectorAll(':scope > details.domain'))
        .sort((a, b) => compareDetails(a, b, mode, 'domainName', 'domainSize'))
        .forEach((node) => list.appendChild(node));
    }}

    function sortAccounts() {{
      const mode = document.getElementById('accountSort').value;
      document.querySelectorAll('details.domain .domain-body').forEach((body) => {{
        Array.from(body.querySelectorAll(':scope > details.account'))
          .sort((a, b) => compareDetails(a, b, mode, 'accountName', 'accountSize'))
          .forEach((node) => body.appendChild(node));
      }});
    }}

    function applyFilter() {{
      const query = document.getElementById('accountFilter').value.trim().toLowerCase();
      const requireAcl = document.getElementById('aclFilter').checked;
      const requireRules = document.getElementById('rulesFilter').checked;
      const requireSettings = document.getElementById('settingsFilter').checked;
      const requireAdmin = document.getElementById('adminFilter').checked;
      
      const allowedTypes = new Set();
      if (document.getElementById('typeAccount').checked) allowedTypes.add('account');
      if (document.getElementById('typeGroup').checked) allowedTypes.add('group');
      if (document.getElementById('typeResource').checked) allowedTypes.add('resource');
      if (document.getElementById('typeForwarder').checked) allowedTypes.add('forwarder');
      
      const filterActive = requireAcl || requireRules || requireSettings || requireAdmin;

      document.querySelectorAll('details.domain').forEach((domain) => {{
        let visible = 0;
        domain.querySelectorAll(':scope .domain-body > details.account').forEach((account) => {{
          const objType = account.dataset.objectType || 'account';
          const typeMatch = allowedTypes.has(objType);
          
          const prefixMatch = !query || (account.dataset.accountName || '').startsWith(query);
          let specialMatch = true;
          if (filterActive) {{
            const hasAcl = account.dataset.hasAcl === 'true';
            const hasRules = account.dataset.hasRules === 'true';
            const hasSettings = account.dataset.hasSettings === 'true';
            const isAdmin = account.dataset.isAdmin === 'true';
            specialMatch = (requireAcl && hasAcl) || (requireRules && hasRules) || (requireSettings && hasSettings) || (requireAdmin && isAdmin);
          }}
          const match = typeMatch && prefixMatch && specialMatch;
          account.classList.toggle('hidden', !match);
          if (match) visible += 1;
        }});
        let domainSpecialMatch = true;
        if (filterActive) {{
          const hasRules = domain.dataset.hasRules === 'true';
          const hasSettings = domain.dataset.hasSettings === 'true';
          domainSpecialMatch = (requireRules && hasRules) || (requireSettings && hasSettings);
        }}
        domain.classList.toggle('hidden', visible === 0 && (query !== '' || !domainSpecialMatch || allowedTypes.size < 4));
      }});
    }}

    document.querySelectorAll('input[type="checkbox"], input[type="search"]').forEach((node) => {{
      node.addEventListener('change', applyFilter);
      node.addEventListener('keyup', applyFilter);
    }});

    document.getElementById('domainSort').addEventListener('change', sortDomains);
    document.getElementById('accountSort').addEventListener('change', sortAccounts);
    document.getElementById('accountFilter').addEventListener('input', applyFilter);
    document.getElementById('aclFilter').addEventListener('change', applyFilter);
    document.getElementById('rulesFilter').addEventListener('change', applyFilter);
    document.getElementById('settingsFilter').addEventListener('change', applyFilter);
    document.getElementById('adminFilter').addEventListener('change', applyFilter);
    document.getElementById('expandAll').addEventListener('click', () => {{
      document.querySelectorAll('details').forEach((node) => node.open = true);
    }});
    document.getElementById('collapseAll').addEventListener('click', () => {{
      document.querySelectorAll('details').forEach((node) => node.open = false);
    }});

    document.querySelectorAll('table.sortable th').forEach((th) => {{
      th.addEventListener('click', () => {{
        const table = th.closest('table');
        const index = Array.from(th.parentNode.children).indexOf(th);
        const tbody = table.querySelector('tbody');
        const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
        th.parentNode.querySelectorAll('th').forEach(h => h.dataset.dir = '');
        th.dataset.dir = dir;
        const rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort((a, b) => {{
          const av = a.children[index].dataset.sort || a.children[index].textContent;
          const bv = b.children[index].dataset.sort || b.children[index].textContent;
          return dir === 'asc' ? collator.compare(av, bv) : collator.compare(bv, av);
        }});
        rows.forEach(row => tbody.appendChild(row));
      }});
    }});
    sortDomains();
    sortAccounts();
  </script>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(document)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect IVA Mail folder inventory through CMD protocol."
    )
    parser.add_argument("--config", help="Path to JSON config file.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--domain", dest="domains", action="append")
    parser.add_argument(
        "--all-domains",
        dest="all_domains",
        action="store_true",
        default=None,
        help="Ignore configured domains and request the domain list with DOMAINSLIST.",
    )
    parser.add_argument(
        "--account",
        dest="accounts",
        action="append",
        help="Account name or account@domain. Can be specified multiple times.",
    )
    parser.add_argument("--output")
    parser.add_argument("--input-json", help="Build report from an existing inventory JSON without connecting to CMD.")
    parser.add_argument("--html-output", help="Write a standalone HTML report in addition to JSON/CSV output.")
    parser.add_argument("--report-title")
    parser.add_argument(
        "--include-imap-stats",
        dest="include_imap_stats",
        action="store_true",
        default=None,
        help="Use IMAP STATUS/FETCH to collect per-folder message count, size, and earliest message date.",
    )
    parser.add_argument(
        "--no-imap-stats",
        dest="include_imap_stats",
        action="store_false",
        default=None,
        help="Disable IMAP pass and collect only data exposed by CMD.",
    )
    parser.add_argument("--imap-host", help="IMAP host. Defaults to --host.")
    parser.add_argument("--imap-port", type=int, help="IMAP port. Defaults to 143 or 993 with --imap-ssl.")
    parser.add_argument(
        "--imap-ssl",
        dest="imap_ssl",
        action="store_true",
        default=None,
        help="Use IMAP over SSL.",
    )
    parser.add_argument(
        "--no-imap-ssl",
        dest="imap_ssl",
        action="store_false",
        default=None,
        help="Use plain IMAP.",
    )
    parser.add_argument("--imap-user", help="IMAP user. Defaults to --user.")
    parser.add_argument("--imap-password", help="IMAP password. Defaults to --password.")
    parser.add_argument(
        "--imap-workers",
        type=int,
        help="Number of parallel persistent IMAP sessions used for folder statistics.",
    )
    parser.add_argument("--format", choices=["json", "csv"])
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--cmd-workers",
        type=int,
        help="Number of parallel persistent CMD sessions used for account, folder info, and ACL collection.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        help="OBJECTSLIST page size. Use 0 to request all objects in one command.",
    )
    parser.add_argument("--max-domains", type=int)
    parser.add_argument("--max-accounts-per-domain", type=int)
    parser.add_argument("--max-mailboxes-per-account", type=int)
    parser.add_argument(
        "--mailbox-class",
        help='Mailbox class filter, usually "mail". Use an empty string to request all classes.',
    )
    parser.add_argument("--include-raw", dest="include_raw", action="store_true", default=None)
    parser.add_argument("--no-acl", dest="include_acl", action="store_false", default=None)
    parser.add_argument(
        "--recalculate-storage",
        dest="recalculate_storage",
        action="store_true",
        default=None,
        help="Pass true to AccountGetMailStorageSize to recalculate mailbox sizes.",
    )
    parser.add_argument(
        "--no-recalculate-storage",
        dest="recalculate_storage",
        action="store_false",
        default=None,
        help="Pass false to AccountGetMailStorageSize and use cached mailbox sizes.",
    )
    parser.add_argument(
        "--include-non-mail-objects",
        dest="include_non_mail_objects",
        action="store_true",
        default=None,
        help="Try MAILBOXESLIST for every domain object, not only account-like objects.",
    )
    parser.add_argument(
        "--include-account-config",
        dest="include_account_config",
        action="store_true",
        default=None,
        help="Read full account config with ObjectGetMultiset for each account.",
    )
    parser.add_argument(
        "--include-mailbox-info",
        dest="include_mailbox_info",
        action="store_true",
        default=None,
        help="Call MboxGetInfo for each folder. Disabled by default because MAILBOXESLIST already returns UID/name.",
    )
    parser.add_argument(
        "--account-storage-source",
        choices=["cmd", "imap"],
        help="Use CMD AccountGetMailStorageSize or sum collected IMAP folder sizes for account totals.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    raw_args = parser.parse_args()

    try:
        config = load_config(raw_args.config)
        opts = merge_args(raw_args, config)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2

    if opts.mailbox_class == "":
        opts.mailbox_class = None
    if opts.account_storage_source not in ("cmd", "imap"):
        print("Failed: account_storage_source must be 'cmd' or 'imap'", file=sys.stderr)
        return 2

    if opts.input_json:
        try:
            with open(opts.input_json, "r", encoding="utf-8") as f:
                result = json.load(f)
            if not opts.html_output:
                print("Failed: --html-output is required with --input-json", file=sys.stderr)
                return 2
            write_html(opts.html_output, result, opts.report_title)
            print(f"Done. HTML report: {opts.html_output}", file=sys.stderr)
            return 0
        except Exception as e:
            print(f"Failed: {e}", file=sys.stderr)
            return 1

    client = CmdClient(opts.host, int(opts.port), float(opts.timeout))
    imap_pool = None
    cmd_pool = None
    try:
        banner = client.connect()
        print(f"Connected: {banner}", file=sys.stderr)
        auth_status = client.authenticate(opts.user, opts.password)
        print(f"Authenticated: {auth_status}", file=sys.stderr)

        cmd_workers = max(1, int(opts.cmd_workers))
        if cmd_workers > 1:
            cmd_pool = CmdClientPool(
                opts.host,
                int(opts.port),
                float(opts.timeout),
                opts.user,
                opts.password,
                cmd_workers,
            )
            print(f"CMD pool configured: workers: {cmd_workers}", file=sys.stderr)

        if opts.include_imap_stats:
            imap_host = opts.imap_host or opts.host
            imap_port = int(opts.imap_port or (993 if opts.imap_ssl else 143))
            imap_user = opts.imap_user or opts.user
            imap_password = opts.imap_password or opts.password
            imap_workers = max(1, int(opts.imap_workers))
            imap_pool = ImapStatsPool(
                imap_host,
                imap_port,
                bool(opts.imap_ssl),
                imap_user,
                imap_password,
                imap_workers,
            )
            print(
                f"IMAP pool configured: {imap_host}:{imap_port}, workers: {imap_workers}",
                file=sys.stderr,
            )

        result = collect_inventory(client, opts, imap_pool, cmd_pool)

        if opts.format == "csv":
            write_csv(opts.output, result["folders"])
            errors_path = os.path.splitext(opts.output)[0] + ".errors.json"
            if result["errors"]:
                write_json(errors_path, {"errors": result["errors"]})
                print(f"Errors written to {errors_path}", file=sys.stderr)
        else:
            write_json(opts.output, result)

        if opts.html_output:
            write_html(opts.html_output, result, opts.report_title)

        print(
            f"Done. Folders: {len(result['folders'])}, errors: {len(result['errors'])}. "
            f"Output: {opts.output}",
            file=sys.stderr,
        )
        if opts.html_output:
            print(f"HTML report: {opts.html_output}", file=sys.stderr)
        return 0 if not result["errors"] else 1
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        return 1
    finally:
        if imap_pool is not None:
            imap_pool.close()
        if cmd_pool is not None:
            cmd_pool.close()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
