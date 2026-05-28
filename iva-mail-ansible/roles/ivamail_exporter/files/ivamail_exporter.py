#!/usr/bin/env python3
"""
IVA Mail Prometheus Exporter
Централизованный экспортер метрик кластера IVA Mail через CMD-протокол (порт 106).

Запуск:
    IVAMAIL_HOSTS=10.3.6.102,10.3.6.103 \
    IVAMAIL_CMD_USER=admin IVAMAIL_CMD_PASSWORD=secret \
    python3 ivamail_exporter.py

Метрики (порт 9118, путь /metrics):
    ivamail_up{host}                      — 1 если нода доступна
    ivamail_info{host,version,...}         — информация о ноде (gauge=1)
    ivamail_uptime_seconds{host}           — аптайм в секундах
    ivamail_accounts_total{host}           — количество аккаунтов
    ivamail_connections_total{host,module} — активные соединения по модулям
    ivamail_queue_pending{host,module}     — письма в очереди по модулям
    ivamail_module_up{host,module}         — статус загруженных модулей
    ivamail_static_var{host,oid}           — числовые StaticVar (если доступны)
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config from env ────────────────────────────────────────────────────────────
_raw_hosts = os.environ.get("IVAMAIL_HOSTS", "")
HOSTS: list[str] = [h.strip() for h in _raw_hosts.split(",") if h.strip()]
CMD_USER: str = os.environ.get("IVAMAIL_CMD_USER", "admin")
CMD_PASSWORD: str = os.environ.get("IVAMAIL_CMD_PASSWORD", "")
CMD_PORT: int = int(os.environ.get("IVAMAIL_CMD_PORT", "106"))
EXPORTER_PORT: int = int(os.environ.get("EXPORTER_PORT", "9118"))
SCRAPE_TIMEOUT: float = float(os.environ.get("SCRAPE_TIMEOUT", "10"))


# ── Minimal CMD protocol client (blocking sockets, no deps) ───────────────────

class CMDError(Exception):
    pass


class CMDConnection:
    """Минимальный клиент CMD-протокола на blocking sockets."""

    CRLF = b"\r\n"
    BODY_END = b"\r\n.\r\n"

    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    def connect(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect((self.host, self.port))
        self._sock = s
        # Читаем приветствие сервера
        greeting = self._read_line()
        if not greeting.startswith("200"):
            raise CMDError(f"Unexpected greeting: {greeting!r}")

    def auth(self, user: str, password: str) -> None:
        # AUTH LOGIN → server prompts Username: → send user → server prompts Password: → send password → 200
        self._send("AUTH LOGIN")
        self._read_line()   # 'Username:' или '334 ...' — просто читаем, не проверяем
        self._send(user)
        self._read_line()   # 'Password:' или '334 ...' — просто читаем, не проверяем
        self._send(password)
        r3 = self._read_line()
        if not r3.startswith("200"):
            raise CMDError(f"Auth failed: {r3!r}")

    def cmd(self, command: str) -> tuple[str, str]:
        """Выполнить команду. Возвращает (code_line, body)."""
        self._send(command)
        code_line = self._read_line()
        body = ""
        if code_line.startswith("200"):
            # Попытаться прочитать тело (может не быть)
            body = self._read_body_optional()
        return code_line, body

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ── Private helpers ──────────────────────────────────────────────────────

    def _send(self, line: str) -> None:
        assert self._sock
        self._sock.sendall((line + "\r\n").encode())

    def _recv_chunk(self) -> bytes:
        assert self._sock
        chunk = self._sock.recv(4096)
        if not chunk:
            raise CMDError("Connection closed by server")
        return chunk

    def _read_line(self) -> str:
        """Читать до \r\n."""
        while self.CRLF not in self._buf:
            self._buf += self._recv_chunk()
        idx = self._buf.index(self.CRLF)
        line = self._buf[:idx].decode(errors="replace")
        self._buf = self._buf[idx + 2:]
        return line.strip()

    def _read_body_optional(self) -> str:
        """Попытаться прочитать многострочное тело (заканчивается \\r\\n.\\r\\n).
        Таймаут 1 сек — если данных нет, тело пустое."""
        assert self._sock
        old_timeout = self._sock.gettimeout()
        self._sock.settimeout(1.0)
        body_buf = self._buf
        try:
            while self.BODY_END not in body_buf:
                body_buf += self._recv_chunk()
        except (socket.timeout, OSError):
            # Нет тела — тип команды без многострочного ответа
            self._sock.settimeout(old_timeout)
            return ""
        self._sock.settimeout(old_timeout)
        end_idx = body_buf.index(self.BODY_END)
        body = body_buf[:end_idx].decode(errors="replace").strip()
        self._buf = body_buf[end_idx + len(self.BODY_END):]
        return body


# ── Metric collection ──────────────────────────────────────────────────────────

Metric = tuple[str, dict[str, str], float, str, str]
# (metric_name, labels, value, help_text, metric_type)


def _parse_uptime(uptime_str: str) -> float:
    """Конвертировать строку вида '2д 3ч 14мин' или '2d 3h 14m' или секунды в float."""
    if not uptime_str:
        return 0.0
    # Числовые секунды
    try:
        return float(uptime_str)
    except ValueError:
        pass
    total = 0.0
    for num, unit in re.findall(r"(\d+)\s*([а-яa-z]+)", uptime_str.lower()):
        n = float(num)
        if unit.startswith(("d", "д")):
            total += n * 86400
        elif unit.startswith(("h", "ч")):
            total += n * 3600
        elif unit.startswith(("m", "м", "mi")):
            total += n * 60
        elif unit.startswith(("s", "с", "se")):
            total += n
    return total


def collect_host(ip: str) -> list[Metric]:
    """Собрать все метрики с одной ноды. Возвращает список Metric-кортежей."""
    metrics: list[Metric] = []
    conn = CMDConnection(ip, CMD_PORT, timeout=SCRAPE_TIMEOUT)

    def _add(name: str, labels: dict[str, str], value: float,
             help_text: str = "", mtype: str = "gauge") -> None:
        metrics.append((name, labels, value, help_text, mtype))

    try:
        conn.connect()
        conn.auth(CMD_USER, CMD_PASSWORD)
    except Exception as exc:
        logger.warning("Cannot connect to %s: %s", ip, exc)
        _add("ivamail_up", {"host": ip}, 0.0, "1 if IVA Mail node is reachable")
        return metrics

    _add("ivamail_up", {"host": ip}, 1.0, "1 if IVA Mail node is reachable")

    try:
        # ── SystemInfo ──────────────────────────────────────────────────────
        code, body = conn.cmd("SystemInfo")
        if code.startswith("200") and body:
            try:
                info = json.loads(body)
                version = str(info.get("Server version", info.get("version", "unknown")))
                cluster = str(info.get("Cluster Status", info.get("cluster_status", "unknown")))
                _add("ivamail_info",
                     {"host": ip, "version": version, "cluster_status": cluster},
                     1.0, "IVA Mail node information")

                uptime_raw = info.get("System UpTime", info.get("uptime", "0"))
                _add("ivamail_uptime_seconds",
                     {"host": ip}, _parse_uptime(str(uptime_raw)),
                     "Node uptime in seconds")

                accounts = info.get("Total Accounts", info.get("accounts", 0))
                try:
                    _add("ivamail_accounts_total",
                         {"host": ip}, float(accounts),
                         "Total user accounts on node", "gauge")
                except (ValueError, TypeError):
                    pass
            except (json.JSONDecodeError, ValueError):
                logger.debug("SystemInfo JSON parse error on %s: %r", ip, body[:80])

        # ── ModulesList ─────────────────────────────────────────────────────
        code, body = conn.cmd("ModulesList")
        modules_loaded: list[str] = []
        if code.startswith("200") and body:
            try:
                raw = json.loads(body)
                if isinstance(raw, list):
                    modules_loaded = [str(m) for m in raw]
                elif isinstance(raw, dict):
                    modules_loaded = list(raw.keys())
            except (json.JSONDecodeError, ValueError):
                modules_loaded = [ln.strip() for ln in body.splitlines() if ln.strip()]

        for mod in modules_loaded:
            _add("ivamail_module_up",
                 {"host": ip, "module": mod}, 1.0,
                 "1 if IVA Mail module is loaded")

        # ── ConnectionsList ─────────────────────────────────────────────────
        code, body = conn.cmd("ConnectionsList")
        if code.startswith("200") and body:
            try:
                raw = json.loads(body)
                # Ожидаем список {module: str, count: int} или dict{module: count}
                if isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict):
                            mod = str(item.get("module", item.get("Module", "unknown")))
                            cnt = item.get("count", item.get("Count", item.get("connections", 0)))
                            try:
                                _add("ivamail_connections_total",
                                     {"host": ip, "module": mod}, float(cnt),
                                     "Active connections per module", "gauge")
                            except (ValueError, TypeError):
                                pass
                elif isinstance(raw, dict):
                    for mod, cnt in raw.items():
                        try:
                            _add("ivamail_connections_total",
                                 {"host": ip, "module": str(mod)}, float(cnt),
                                 "Active connections per module", "gauge")
                        except (ValueError, TypeError):
                            pass
            except (json.JSONDecodeError, ValueError):
                pass

        # ── MailQueueList ───────────────────────────────────────────────────
        code, body = conn.cmd("MailQueueList")
        if code.startswith("200") and body:
            try:
                raw = json.loads(body)
                # Ожидаем {module: {pending: int, ...}} или список
                if isinstance(raw, dict):
                    for mod, qinfo in raw.items():
                        if isinstance(qinfo, dict):
                            pending = qinfo.get("pending", qinfo.get("count", 0))
                        else:
                            pending = qinfo
                        try:
                            _add("ivamail_queue_pending",
                                 {"host": ip, "module": str(mod)}, float(pending),
                                 "Pending messages in mail queue", "gauge")
                        except (ValueError, TypeError):
                            pass
                elif isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict):
                            mod = str(item.get("module", item.get("Module", "unknown")))
                            pending = item.get("pending", item.get("count", 0))
                            try:
                                _add("ivamail_queue_pending",
                                     {"host": ip, "module": mod}, float(pending),
                                     "Pending messages in mail queue", "gauge")
                            except (ValueError, TypeError):
                                pass
            except (json.JSONDecodeError, ValueError):
                pass

        # ── GetStaticVar "1" ────────────────────────────────────────────────
        # Иерархические статистические переменные IVA Mail (SNMP-подобный OID формат)
        code, body = conn.cmd('GetStaticVar "1"')
        if code.startswith("200") and body:
            _collect_static_vars(conn, ip, "1", body, metrics, depth=0)

    except Exception as exc:
        logger.error("Error collecting metrics from %s: %s", ip, exc)
    finally:
        conn.close()

    return metrics


def _collect_static_vars(conn: CMDConnection, ip: str, root_oid: str,
                          body: str, metrics: list[Metric], depth: int) -> None:
    """Рекурсивно собрать числовые StaticVar значения, не углубляясь > 3 уровней."""
    if depth > 3:
        return
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        # Попытка прочитать как число
        try:
            val = float(body.strip())
            metrics.append((
                "ivamail_static_var",
                {"host": ip, "oid": root_oid},
                val,
                "IVA Mail static variable",
                "gauge",
            ))
        except (ValueError, TypeError):
            pass
        return

    if isinstance(parsed, list):
        # Список дочерних OID-суффиксов — рекурсируем
        for sub in parsed[:50]:  # ограничение на количество под-OID
            child_oid = f"{root_oid}.{sub}"
            try:
                code, child_body = conn.cmd(f'GetStaticVar "{child_oid}"')
                if code.startswith("200") and child_body:
                    _collect_static_vars(conn, ip, child_oid, child_body,
                                         metrics, depth + 1)
            except Exception:
                break
    elif isinstance(parsed, dict):
        for k, v in list(parsed.items())[:50]:
            try:
                val = float(v)
                metrics.append((
                    "ivamail_static_var",
                    {"host": ip, "oid": f"{root_oid}.{k}"},
                    val,
                    "IVA Mail static variable",
                    "gauge",
                ))
            except (ValueError, TypeError):
                pass
    else:
        try:
            val = float(parsed)
            metrics.append((
                "ivamail_static_var",
                {"host": ip, "oid": root_oid},
                val,
                "IVA Mail static variable",
                "gauge",
            ))
        except (ValueError, TypeError):
            pass


# ── Prometheus text format ─────────────────────────────────────────────────────

def _labels_str(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = []
    for k, v in labels.items():
        escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{k}="{escaped}"')
    return "{" + ",".join(parts) + "}"


def format_metrics(all_metrics: list[Metric]) -> str:
    """Преобразовать список метрик в текстовый формат Prometheus."""
    lines: list[str] = []
    # Группируем по имени для корректного вывода # HELP / # TYPE
    seen: dict[str, bool] = {}
    for name, labels, value, help_text, mtype in all_metrics:
        if name not in seen:
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
            seen[name] = True
        label_str = _labels_str(labels)
        lines.append(f"{name}{label_str} {value}")
    return "\n".join(lines) + "\n"


# ── HTTP server ────────────────────────────────────────────────────────────────

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in ("/metrics", "/metrics/"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found\n")
            return

        t0 = time.monotonic()
        all_metrics: list[Metric] = []

        if not HOSTS:
            # Нет хостов — вернуть только метаметрику
            all_metrics.append((
                "ivamail_exporter_hosts_total", {},
                0.0, "Total configured IVA Mail hosts", "gauge",
            ))
        else:
            # Параллельный опрос всех хостов
            with ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
                futures = {pool.submit(collect_host, ip): ip for ip in HOSTS}
                for fut in as_completed(futures):
                    try:
                        all_metrics.extend(fut.result())
                    except Exception as exc:
                        ip = futures[fut]
                        logger.error("collect_host(%s) raised: %s", ip, exc)
                        all_metrics.append((
                            "ivamail_up", {"host": ip}, 0.0,
                            "1 if IVA Mail node is reachable", "gauge",
                        ))

        elapsed = time.monotonic() - t0
        all_metrics.append((
            "ivamail_scrape_duration_seconds", {},
            elapsed, "Duration of IVA Mail metrics scrape", "gauge",
        ))
        all_metrics.append((
            "ivamail_exporter_hosts_total", {},
            float(len(HOSTS)), "Total configured IVA Mail hosts", "gauge",
        ))

        body = format_metrics(all_metrics).encode()
        self.send_response(200)
        self.send_header("Content-Type",
                         "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        logger.info("Scraped %d hosts in %.2fs, %d metrics",
                    len(HOSTS), elapsed, len(all_metrics))

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN401
        pass  # Подавить стандартный HTTP-лог — используем свой


def main() -> None:
    logger.info("IVA Mail Exporter starting on :%d", EXPORTER_PORT)
    logger.info("Configured hosts (%d): %s", len(HOSTS), ", ".join(HOSTS) or "(none)")
    server = HTTPServer(("", EXPORTER_PORT), MetricsHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
