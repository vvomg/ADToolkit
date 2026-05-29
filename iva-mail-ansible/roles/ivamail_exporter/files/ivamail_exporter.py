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
    ivamail_queue_pending{host,module}              — письма в очереди по модулям
    ivamail_module_up{host,module}                  — статус загруженных модулей
    ivamail_network_tcp_sent_bytes_total{host}       — TCP байт отправлено
    ivamail_network_tcp_received_bytes_total{host}   — TCP байт получено
    ivamail_dsn_delivered_total{host}               — DSN: доставленных
    ivamail_dsn_failed_total{host}                  — DSN: ошибок доставки
    ivamail_dsn_delayed_total{host}                 — DSN: отложенных
    ivamail_queue_outgoing_messages_total{host}     — исходящих сообщений в очереди
    ivamail_queue_outgoing_size_bytes_total{host}   — размер очереди, байт
    ivamail_queue_recipients_total{host}            — получателей в очереди
    ivamail_delivery_incoming_messages_total{host}  — входящих сообщений всего
    ivamail_delivery_incoming_size_bytes_total{host}— входящих байт всего
    ivamail_sessions_active{host}                   — активных сессий
    ivamail_sessions_closed_total{host}             — закрытых сессий всего
    ivamail_auth_failed_total{host}                 — неудачных аутентификаций
    ivamail_auth_success_total{host}                — успешных аутентификаций
    ivamail_cluster_connections{host,node,role,status} — соединений на узле кластера
    ivamail_cluster_objects{host,node,role,status}  — объектов на узле кластера
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
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
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
        """Read optional multi-line JSON body using brace/bracket depth tracking.

        IVA Mail CMD sends pretty-printed JSON after '200 OK'. Body ends when the
        opening brace/bracket depth returns to zero. Uses a short initial peek (0.3s)
        so commands without a body return quickly.

        Depth tracking handles nested objects (e.g. ConnectionsList has connection
        objects nested inside per-module arrays) correctly.
        """
        assert self._sock
        old_timeout = self._sock.gettimeout()

        def _count_depth(s: str) -> int:
            return sum(1 if c in ('{', '[') else -1 if c in ('}', ']') else 0
                       for c in s)

        # Quick peek: detect whether a body is coming at all
        self._sock.settimeout(0.3)
        try:
            first_line = self._read_line()
        except (socket.timeout, OSError):
            self._sock.settimeout(old_timeout)
            return ""

        if not first_line.strip():
            self._sock.settimeout(old_timeout)
            return ""

        lines = [first_line]
        depth = _count_depth(first_line)
        if depth <= 0:
            self._sock.settimeout(old_timeout)
            return first_line

        # Body is arriving — read until depth returns to 0
        self._sock.settimeout(max(old_timeout or 10.0, 5.0))
        try:
            while depth > 0:
                line = self._read_line()
                lines.append(line)
                depth += _count_depth(line)
        except (socket.timeout, OSError):
            pass

        self._sock.settimeout(old_timeout)
        return "\n".join(lines)


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
        logger.debug("SystemInfo %s → code=%r body_len=%d body_preview=%r",
                     ip, code, len(body), body[:120])
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
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("SystemInfo JSON parse error on %s: %s | body=%r",
                               ip, exc, body[:200])

        # ── ModulesList ─────────────────────────────────────────────────────
        code, body = conn.cmd("ModulesList")
        logger.debug("ModulesList %s → code=%r body_len=%d body_preview=%r",
                     ip, code, len(body), body[:120])
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
        logger.debug("ConnectionsList %s → code=%r body_len=%d body_preview=%r",
                     ip, code, len(body), body[:120])
        if code.startswith("200") and body:
            try:
                raw = json.loads(body)
                # Actual format: {"ModuleName": [conn_obj, ...], ...}
                # conn_obj has Active, Local, Phase, Remote, Secure fields
                if isinstance(raw, dict):
                    for mod, val in raw.items():
                        try:
                            cnt = float(len(val)) if isinstance(val, list) else float(val)
                            _add("ivamail_connections_total",
                                 {"host": ip, "module": str(mod)}, cnt,
                                 "Active connections per module", "gauge")
                        except (ValueError, TypeError):
                            pass
                elif isinstance(raw, list):
                    for item in raw:
                        if isinstance(item, dict):
                            mod = str(item.get("module", item.get("Module", "unknown")))
                            cnt = item.get("count", item.get("Count", 0))
                            try:
                                _add("ivamail_connections_total",
                                     {"host": ip, "module": mod}, float(cnt),
                                     "Active connections per module", "gauge")
                            except (ValueError, TypeError):
                                pass
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("ConnectionsList parse error %s: %s | body=%r", ip, exc, body[:200])

        # ── MailQueueList ───────────────────────────────────────────────────
        code, body = conn.cmd("MailQueueList")
        logger.debug("MailQueueList %s → code=%r body_len=%d body_preview=%r",
                     ip, code, len(body), body[:120])
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

        # ── StaticVars (через ListStaticVars → GetStaticVar) ────────────────
        # GetStaticVar "1" возвращает 204 OK (parent-нода без значения).
        # Правильный путь: ListStaticVars для обхода дерева, GetStaticVar для листьев.
        _collect_static_vars_v2(conn, ip, metrics)

        # ── ClusterGetStats ──────────────────────────────────────────────────
        code, body = conn.cmd("ClusterGetStats")
        logger.debug("ClusterGetStats %s → code=%r body_len=%d body_preview=%r",
                     ip, code, len(body), body[:120])
        if code.startswith("200") and body:
            try:
                cluster = json.loads(body)
                if isinstance(cluster, dict):
                    for node_key, node_info in cluster.items():
                        if not isinstance(node_info, dict):
                            continue
                        addr = node_info.get("Address", node_key)
                        # Извлечь IP из "/I [10.3.6.206]" → "10.3.6.206"
                        import re as _re
                        m = _re.search(r'\[([0-9.]+)\]', addr)
                        node_ip = m.group(1) if m else addr
                        role = str(node_info.get("Role", "unknown"))
                        status = str(node_info.get("Status", "unknown"))
                        labels = {"host": ip, "node": node_ip,
                                  "role": role, "status": status}
                        try:
                            _add("ivamail_cluster_connections",
                                 labels,
                                 float(node_info.get("TotalConnections", 0)),
                                 "Total connections on cluster node", "gauge")
                            _add("ivamail_cluster_objects",
                                 labels,
                                 float(node_info.get("TotalObjects", 0)),
                                 "Total objects on cluster node", "gauge")
                        except (ValueError, TypeError):
                            pass
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("ClusterGetStats parse error on %s: %s", ip, exc)

    except Exception as exc:
        logger.error("Error collecting metrics from %s: %s", ip, exc)
    finally:
        conn.close()

    return metrics


# Статический маппинг всех известных OID → (metric_name, help, type)
_STATIC_VAR_MAP: dict[str, tuple[str, str, str]] = {
    "1.101.1": ("ivamail_network_tcp_sent_bytes_total",
                "Total TCP bytes sent", "counter"),
    "1.101.2": ("ivamail_network_tcp_received_bytes_total",
                "Total TCP bytes received", "counter"),
    "1.101.3": ("ivamail_network_udp_sent_bytes_total",
                "Total UDP bytes sent", "counter"),
    "1.101.4": ("ivamail_network_udp_received_bytes_total",
                "Total UDP bytes received", "counter"),
    "1.102.1": ("ivamail_dsn_delivered_total",
                "DSN delivered reports sent", "counter"),
    "1.102.2": ("ivamail_dsn_failed_total",
                "DSN failed reports sent", "counter"),
    "1.102.3": ("ivamail_dsn_delayed_total",
                "DSN delayed reports sent", "counter"),
    "1.103.1": ("ivamail_queue_outgoing_messages_total",
                "Queued outgoing messages count", "gauge"),
    "1.103.2": ("ivamail_queue_outgoing_size_bytes_total",
                "Queued outgoing messages size in bytes", "gauge"),
    "1.103.3": ("ivamail_queue_recipients_total",
                "Queued recipients count", "gauge"),
    "1.104.1": ("ivamail_delivery_incoming_messages_total",
                "Incoming messages count", "counter"),
    "1.104.2": ("ivamail_delivery_incoming_size_bytes_total",
                "Incoming messages size in bytes", "counter"),
    "1.105.1": ("ivamail_sessions_active",
                "Active sessions", "gauge"),
    "1.105.2": ("ivamail_sessions_closed_total",
                "Closed sessions total", "counter"),
    "1.106.1": ("ivamail_auth_failed_total",
                "Failed authentication attempts", "counter"),
    "1.106.2": ("ivamail_auth_success_total",
                "Successful authentication attempts", "counter"),
}


def _collect_static_vars_v2(conn: CMDConnection, ip: str,
                             metrics: list[Metric]) -> None:
    """Собрать StaticVar метрики через ListStaticVars + GetStaticVar.

    GetStaticVar "1" → 204 OK (parent без значения), поэтому используем
    ListStaticVars для обхода, GetStaticVar только на листьях.
    """
    try:
        # Получить группы верхнего уровня
        code, body = conn.cmd('ListStaticVars "1"')
        logger.debug("ListStaticVars 1 %s → code=%r body_len=%d",
                     ip, code, len(body))
        if not code.startswith("200") or not body:
            return
        groups = json.loads(body)
    except Exception as exc:
        logger.debug("ListStaticVars root error on %s: %s", ip, exc)
        return

    for grp in groups:
        if not isinstance(grp, list) or len(grp) < 1:
            continue
        gid = grp[0]
        group_oid = f"1.{gid}"
        try:
            code2, body2 = conn.cmd(f'ListStaticVars "{group_oid}"')
            if not code2.startswith("200") or not body2:
                continue
            leaves = json.loads(body2)
        except Exception:
            continue

        for leaf in leaves:
            if not isinstance(leaf, list) or len(leaf) < 1:
                continue
            lid = leaf[0]
            leaf_oid = f"{group_oid}.{lid}"
            try:
                code3, val_body = conn.cmd(f'GetStaticVar "{leaf_oid}"')
                if not code3.startswith("200") or not val_body:
                    continue
                val = float(val_body.strip())
            except Exception:
                continue

            if leaf_oid in _STATIC_VAR_MAP:
                mname, help_text, mtype = _STATIC_VAR_MAP[leaf_oid]
            else:
                # Неизвестный OID — fallback с универсальным именем
                mname = "ivamail_static_var"
                help_text = "IVA Mail static variable"
                mtype = "gauge"

            metrics.append((mname, {"host": ip}, val, help_text, mtype))
            logger.debug("StaticVar %s oid=%s → %s = %s",
                         ip, leaf_oid, mname, val)


def _collect_static_vars(conn: CMDConnection, ip: str, root_oid: str,
                          body: str, metrics: list[Metric], depth: int) -> None:
    """Устаревшая рекурсивная функция — оставлена для совместимости."""
    # Заменена _collect_static_vars_v2. Код ниже не вызывается из collect_host.
    if depth > 3:
        return
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        try:
            val = float(body.strip())
            metrics.append((
                "ivamail_static_var",
                {"host": ip, "oid": root_oid},
                val, "IVA Mail static variable", "gauge",
            ))
        except (ValueError, TypeError):
            pass
        return

    if isinstance(parsed, list):
        for sub in parsed[:50]:
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
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("", EXPORTER_PORT), MetricsHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
