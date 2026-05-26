"""
ADToolKit — Windows Installer
==============================
Мастер установки ADToolKit на Linux-сервер по SSH.

Сборка в .exe:
    pip install pyinstaller paramiko cryptography
    pyinstaller --onefile --windowed --name ADToolKit-Setup ^
        --add-data "deploy.zip;." installer.py

Требования для разработки:
    pip install paramiko cryptography
    python installer.py
"""

import os
import re
import sys
import io
import socket
import threading
import time
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable

# Регулярка для удаления ANSI escape-кодов (цвета терминала)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mABCDEFGHJKSTflnprsu]")

# ── Paramiko ──────────────────────────────────────────────────────────────────
try:
    import paramiko
except ImportError:
    tk.Tk().withdraw()
    messagebox.showerror(
        "Ошибка зависимостей",
        "Библиотека paramiko не установлена.\n\n"
        "Выполните: pip install paramiko cryptography",
    )
    sys.exit(1)

# ── Путь к deploy.zip (внутри .exe или рядом со скриптом) ─────────────────────
if getattr(sys, "frozen", False):
    BUNDLE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

DEPLOY_ZIP_PATH = os.path.join(BUNDLE_DIR, "deploy.zip")

# ── Версия ────────────────────────────────────────────────────────────────────
VERSION = "0.1.0"

# ── Цвета темы ────────────────────────────────────────────────────────────────
COLORS = {
    "bg":       "#1e1e2e",  # Catppuccin Macchiato base
    "surface":  "#24273a",
    "panel":    "#2a2d3e",
    "accent":   "#8aadf4",  # blue
    "green":    "#a6e3a1",
    "red":      "#f38ba8",
    "yellow":   "#f9e2af",
    "text":     "#cad3f5",
    "subtext":  "#a5adcb",
    "overlay":  "#6e738d",
    "border":   "#363a4f",
    "btn_bg":   "#8aadf4",
    "btn_fg":   "#1e1e2e",
    "btn_dis":  "#494d64",
}

# ═══════════════════════════════════════════════════════════════════════════════
#  SSH / Deployment helpers
# ═══════════════════════════════════════════════════════════════════════════════

class SSHSession:
    """Тонкая обёртка над paramiko.SSHClient."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host     = host
        self.port     = port
        self.username = username
        self.password = password
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        self._client = client

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def run(self, cmd: str, log: Callable[[str], None],
            stdin_data: bytes = b"") -> int:
        """Выполнить команду с live-выводом в log callback. Вернуть exit code.

        stdin_data — байты, которые нужно послать на stdin сразу после старта
        (используется для sudo -S: передаём пароль).
        """
        assert self._client
        transport = self._client.get_transport()
        assert transport
        chan = transport.open_session()
        chan.get_pty()
        chan.exec_command(cmd)

        # Отправляем пароль sudo до того как sudo закроет stdin
        if stdin_data:
            time.sleep(0.15)
            chan.sendall(stdin_data)

        buf = ""
        while True:
            if chan.recv_ready():
                chunk = chan.recv(4096).decode("utf-8", errors="replace")
                buf += chunk
                # Выводим построчно, не дожидаясь \n на последней неполной строке
                lines = buf.split("\n")
                buf = lines.pop()          # последний фрагмент без \n — держим в буфере
                for line in lines:
                    cleaned = line.rstrip("\r")
                    if cleaned.strip():
                        log(cleaned)
            if chan.exit_status_ready() and not chan.recv_ready():
                break
            time.sleep(0.05)

        # Дослить остаток буфера
        if buf.strip():
            log(buf.rstrip("\r\n"))

        return chan.recv_exit_status()

    def upload(self, local_path: str, remote_path: str, log: Callable[[str], None]) -> None:
        """Загрузить файл на сервер через SFTP."""
        assert self._client
        sftp = self._client.open_sftp()
        size = os.path.getsize(local_path)
        log(f"Загрузка {os.path.basename(local_path)} ({size // 1024} КБ)...")

        uploaded = [0]

        def progress(sent: int, total: int) -> None:
            pct = int(sent / total * 100)
            if pct % 10 == 0 and pct != (uploaded[0] // (total // 10) * 10 if total else 0):
                pass  # throttle
            uploaded[0] = sent

        sftp.put(local_path, remote_path, callback=lambda s, t: progress(s, t))
        sftp.close()
        log(f"Файл загружен: {remote_path}")


def test_connection(host: str, port: int, user: str, password: str) -> tuple[bool, str]:
    """Проверить SSH-подключение. Вернуть (ok, message)."""
    try:
        sess = SSHSession(host, port, user, password)
        sess.connect()
        # Получить версию ОС
        _, stdout, _ = sess._client.exec_command("cat /etc/os-release | grep PRETTY_NAME | head -1")  # type: ignore
        os_info = stdout.read().decode().strip().replace('PRETTY_NAME=', '').strip('"')
        sess.close()
        return True, os_info or "Linux"
    except paramiko.AuthenticationException:
        return False, "Ошибка аутентификации: неверный логин или пароль"
    except (socket.timeout, TimeoutError):
        return False, f"Превышено время ожидания подключения к {host}:{port}"
    except Exception as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
#  GUI — Main Window
# ═══════════════════════════════════════════════════════════════════════════════

class InstallerApp(tk.Tk):

    def __init__(self):
        super().__init__()

        self.title(f"ADToolKit Setup v{VERSION}")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])

        # Центрировать окно
        w, h = 640, 500
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # Состояние
        self.host     = tk.StringVar(value="10.3.6.100")
        self.port     = tk.StringVar(value="22")
        self.username = tk.StringVar(value="root")
        self.password = tk.StringVar(value="")
        self.web_port = tk.StringVar(value="80")

        self._session: SSHSession | None = None

        self._build_ui()
        self._show_page("welcome")

    # ── UI Build ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Header
        hdr = tk.Frame(self, bg=COLORS["surface"], height=70)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="⚙  ADToolKit",
                 font=("Segoe UI", 18, "bold"),
                 bg=COLORS["surface"], fg=COLORS["accent"]).pack(side="left", padx=20, pady=15)
        tk.Label(hdr, text=f"Мастер установки v{VERSION}",
                 font=("Segoe UI", 9),
                 bg=COLORS["surface"], fg=COLORS["overlay"]).pack(side="right", padx=20)

        # Separator
        tk.Frame(self, bg=COLORS["border"], height=1).pack(fill="x")

        # Step indicator
        self._step_frame = tk.Frame(self, bg=COLORS["bg"], height=36)
        self._step_frame.pack(fill="x", padx=20, pady=(8, 0))
        self._step_frame.pack_propagate(False)
        self._step_lbl = tk.Label(
            self._step_frame, text="", font=("Segoe UI", 9),
            bg=COLORS["bg"], fg=COLORS["overlay"],
        )
        self._step_lbl.pack(side="left")

        # Footer buttons — пакуем ПЕРВЫМИ с side="bottom", иначе expand=True
        # у content-фрейма съест всё пространство и footer будет невидим.
        tk.Frame(self, bg=COLORS["border"], height=1).pack(side="bottom", fill="x")
        footer = tk.Frame(self, bg=COLORS["surface"], height=52)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)

        self._btn_back = self._make_btn(footer, "← Назад",  self._go_back,  secondary=True)
        self._btn_back.pack(side="right", padx=(4, 16), pady=10)

        self._btn_next = self._make_btn(footer, "Далее →",  self._go_next)
        self._btn_next.pack(side="right", padx=4, pady=10)

        self._btn_cancel = self._make_btn(footer, "Отмена", self._on_cancel, secondary=True)
        self._btn_cancel.pack(side="left", padx=16, pady=10)

        # Content area — пакуем ПОСЛЕДНИМ, чтобы занять оставшееся пространство
        self._content = tk.Frame(self, bg=COLORS["bg"])
        self._content.pack(fill="both", expand=True, padx=20, pady=4)

    def _make_btn(self, parent, text: str, cmd, secondary=False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=cmd,
            font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2",
            padx=14, pady=5,
            bg=COLORS["panel"] if secondary else COLORS["btn_bg"],
            fg=COLORS["subtext"] if secondary else COLORS["btn_fg"],
            activebackground=COLORS["border"] if secondary else COLORS["accent"],
            activeforeground=COLORS["text"] if secondary else COLORS["btn_fg"],
        )

    def _label(self, parent, text: str, **kw) -> tk.Label:
        return tk.Label(parent, text=text, bg=COLORS["bg"], fg=COLORS["text"],
                        font=("Segoe UI", 10), **kw)

    def _entry(self, parent, var: tk.StringVar, show="", width=30) -> tk.Entry:
        return tk.Entry(parent, textvariable=var, show=show, width=width,
                        font=("Consolas", 10),
                        bg=COLORS["panel"], fg=COLORS["text"],
                        insertbackground=COLORS["accent"],
                        relief="flat", bd=0,
                        highlightthickness=1,
                        highlightcolor=COLORS["accent"],
                        highlightbackground=COLORS["border"])

    # ── Page navigation ────────────────────────────────────────────────────────

    PAGES    = ["welcome", "connection", "install", "done"]
    TITLES   = ["Добро пожаловать", "Подключение к серверу", "Установка", "Готово"]

    def _show_page(self, name: str) -> None:
        self._current_page = name
        for w in self._content.winfo_children():
            w.destroy()

        idx = self.PAGES.index(name)
        total = len(self.PAGES)
        self._step_lbl.config(
            text=f"Шаг {idx + 1} из {total}  —  {self.TITLES[idx]}"
        )

        getattr(self, f"_page_{name}")()
        self._update_buttons()

    def _update_buttons(self) -> None:
        idx = self.PAGES.index(self._current_page)
        self._btn_back.config(
            state="normal" if idx > 0 else "disabled",
            bg=COLORS["panel"] if idx > 0 else COLORS["btn_dis"],
        )
        if self._current_page == "done":
            self._btn_next.config(text="Закрыть", command=self.destroy, state="normal",
                                  bg=COLORS["green"])
        elif self._current_page == "install":
            self._btn_next.config(state="disabled", text="Далее →",
                                  bg=COLORS["btn_dis"])
        else:
            self._btn_next.config(text="Далее →", command=self._go_next,
                                  state="normal", bg=COLORS["btn_bg"])

    def _go_next(self) -> None:
        idx = self.PAGES.index(self._current_page)
        if self._current_page == "connection":
            if not self._validate_connection_fields():
                return
        if idx + 1 < len(self.PAGES):
            self._show_page(self.PAGES[idx + 1])

    def _go_back(self) -> None:
        idx = self.PAGES.index(self._current_page)
        if idx > 0:
            self._show_page(self.PAGES[idx - 1])

    def _on_cancel(self) -> None:
        if messagebox.askyesno("Отмена", "Прервать установку и выйти?"):
            self.destroy()

    # ══════════════════════════════════════════════════════════════════════════
    #  Page: Welcome
    # ══════════════════════════════════════════════════════════════════════════

    def _page_welcome(self) -> None:
        f = self._content

        tk.Label(f, text="Добро пожаловать в установщик ADToolKit",
                 font=("Segoe UI", 14, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor="w", pady=(20, 8))

        tk.Label(f, text=(
            "ADToolKit — веб-интерфейс для управления кластером IVA Mail.\n"
            "Мастер установит приложение на Linux-сервер по SSH."
        ), font=("Segoe UI", 10), bg=COLORS["bg"], fg=COLORS["subtext"],
            justify="left", wraplength=580).pack(anchor="w", pady=(0, 20))

        # Requirements box
        req_frame = tk.Frame(f, bg=COLORS["panel"], padx=16, pady=12)
        req_frame.pack(fill="x", pady=(0, 16))

        tk.Label(req_frame, text="Что будет установлено:",
                 font=("Segoe UI", 9, "bold"),
                 bg=COLORS["panel"], fg=COLORS["accent"]).pack(anchor="w")

        items = [
            ("Python 3.11 + virtualenv",  "зависимости FastAPI"),
            ("nginx",                      "веб-сервер и обратный прокси"),
            ("ansible",                    "для запуска плейбуков с сервера"),
            ("ADToolKit backend",          "FastAPI, uvicorn, systemd-сервис"),
            ("ADToolKit frontend",         "React SPA (статика через nginx)"),
        ]
        for name, desc in items:
            row = tk.Frame(req_frame, bg=COLORS["panel"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text="✓", font=("Segoe UI", 10),
                     bg=COLORS["panel"], fg=COLORS["green"]).pack(side="left", padx=(0, 8))
            tk.Label(row, text=name, font=("Segoe UI", 9, "bold"),
                     bg=COLORS["panel"], fg=COLORS["text"]).pack(side="left")
            tk.Label(row, text=f" — {desc}", font=("Segoe UI", 9),
                     bg=COLORS["panel"], fg=COLORS["overlay"]).pack(side="left")

        # Deploy zip warning
        if not os.path.exists(DEPLOY_ZIP_PATH):
            warn_frame = tk.Frame(f, bg="#3d1a1a", padx=12, pady=8)
            warn_frame.pack(fill="x", pady=(0, 8))
            tk.Label(warn_frame,
                     text="⚠  deploy.zip не найден. Запустите build.bat перед установкой.",
                     font=("Segoe UI", 9),
                     bg="#3d1a1a", fg=COLORS["red"]).pack(anchor="w")
        else:
            zip_size = os.path.getsize(DEPLOY_ZIP_PATH) // 1024
            ok_frame = tk.Frame(f, bg="#1a2d1a", padx=12, pady=8)
            ok_frame.pack(fill="x", pady=(0, 8))
            tk.Label(ok_frame,
                     text=f"✓  Пакет установки найден ({zip_size} КБ)",
                     font=("Segoe UI", 9),
                     bg="#1a2d1a", fg=COLORS["green"]).pack(anchor="w")

    # ══════════════════════════════════════════════════════════════════════════
    #  Page: Connection
    # ══════════════════════════════════════════════════════════════════════════

    def _page_connection(self) -> None:
        f = self._content

        tk.Label(f, text="Параметры SSH-подключения",
                 font=("Segoe UI", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor="w", pady=(16, 4))
        tk.Label(f, text="Введите данные для подключения к целевому серверу.",
                 font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["subtext"]).pack(anchor="w", pady=(0, 16))

        form = tk.Frame(f, bg=COLORS["bg"])
        form.pack(fill="x")

        def field(label: str, var: tk.StringVar, row: int, show="") -> None:
            tk.Label(form, text=label, font=("Segoe UI", 9),
                     bg=COLORS["bg"], fg=COLORS["subtext"],
                     width=18, anchor="e").grid(row=row, column=0, pady=6, padx=(0, 10), sticky="e")
            e = self._entry(form, var, show=show, width=36)
            e.grid(row=row, column=1, pady=6, sticky="w")

        field("IP-адрес сервера:",  self.host,     0)
        field("SSH порт:",          self.port,     1)
        field("Пользователь SSH:",  self.username, 2)
        field("Пароль SSH:",        self.password, 3, show="•")

        # Web port
        tk.Label(form, text="Порт веб-интерфейса:",
                 font=("Segoe UI", 9), bg=COLORS["bg"], fg=COLORS["subtext"],
                 width=18, anchor="e").grid(row=4, column=0, pady=6, padx=(0, 10), sticky="e")
        e = self._entry(form, self.web_port, width=10)
        e.grid(row=4, column=1, pady=6, sticky="w")

        # Test connection button + result
        self._conn_status_var = tk.StringVar(value="")
        self._conn_status_color = tk.StringVar(value=COLORS["overlay"])

        test_row = tk.Frame(f, bg=COLORS["bg"])
        test_row.pack(fill="x", pady=(12, 4))

        self._btn_test = self._make_btn(test_row, "🔗  Проверить подключение",
                                        self._test_connection, secondary=True)
        self._btn_test.pack(side="left")

        self._conn_lbl = tk.Label(test_row, textvariable=self._conn_status_var,
                                  font=("Segoe UI", 9), bg=COLORS["bg"],
                                  fg=COLORS["overlay"])
        self._conn_lbl.pack(side="left", padx=12)

    def _validate_connection_fields(self) -> bool:
        if not self.host.get().strip():
            messagebox.showwarning("Ввод", "Введите IP-адрес сервера.")
            return False
        try:
            p = int(self.port.get())
            if not 1 <= p <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Ввод", "Порт должен быть числом от 1 до 65535.")
            return False
        if not self.username.get().strip():
            messagebox.showwarning("Ввод", "Введите имя пользователя SSH.")
            return False
        if not self.password.get():
            messagebox.showwarning("Ввод", "Введите пароль SSH.")
            return False
        return True

    def _test_connection(self) -> None:
        if not self._validate_connection_fields():
            return
        self._btn_test.config(state="disabled", text="Подключение...")
        self._conn_status_var.set("Проверяем...")
        self._conn_lbl.config(fg=COLORS["yellow"])

        def _run():
            ok, msg = test_connection(
                self.host.get().strip(),
                int(self.port.get()),
                self.username.get().strip(),
                self.password.get(),
            )
            self.after(0, lambda: self._on_test_result(ok, msg))

        threading.Thread(target=_run, daemon=True).start()

    def _on_test_result(self, ok: bool, msg: str) -> None:
        self._btn_test.config(state="normal", text="🔗  Проверить подключение")
        if ok:
            self._conn_status_var.set(f"✓  Подключено  ·  {msg}")
            self._conn_lbl.config(fg=COLORS["green"])
        else:
            self._conn_status_var.set(f"✗  {msg}")
            self._conn_lbl.config(fg=COLORS["red"])

    # ══════════════════════════════════════════════════════════════════════════
    #  Page: Install
    # ══════════════════════════════════════════════════════════════════════════

    def _page_install(self) -> None:
        f = self._content

        # Заголовок
        hdr = tk.Frame(f, bg=COLORS["bg"])
        hdr.pack(fill="x", pady=(12, 8))

        tk.Label(hdr, text="Установка ADToolKit",
                 font=("Segoe UI", 13, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(side="left")

        self._install_status_lbl = tk.Label(hdr, text="Подготовка...",
                                             font=("Segoe UI", 9),
                                             bg=COLORS["bg"], fg=COLORS["overlay"])
        self._install_status_lbl.pack(side="right")

        # Progress bar — пакуем СНИЗУ до лога, чтобы лог не вытеснил их
        self._progress = ttk.Progressbar(f, mode="indeterminate")
        self._progress.pack(side="bottom", fill="x", pady=(4, 0))

        # Log terminal — занимает всё оставшееся место
        log_frame = tk.Frame(f, bg=COLORS["panel"], padx=1, pady=1)
        log_frame.pack(fill="both", expand=True)

        self._log_text = tk.Text(
            log_frame,
            font=("Consolas", 9),
            bg="#0d1117", fg="#c9d1d9",
            insertbackground=COLORS["accent"],
            relief="flat", bd=0,
            wrap="word",
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical",
                                  command=self._log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self._log_text.pack(fill="both", expand=True, padx=1, pady=1)
        self._log_text.config(yscrollcommand=scrollbar.set)

        # Tag colors for terminal
        self._log_text.tag_config("info",    foreground="#58a6ff")
        self._log_text.tag_config("ok",      foreground="#3fb950")
        self._log_text.tag_config("warn",    foreground="#d29922")
        self._log_text.tag_config("err",     foreground="#f85149")
        self._log_text.tag_config("default", foreground="#c9d1d9")

        # Автозапуск установки через 400 мс (после того как UI отрисуется)
        self.after(400, self._start_install)

    def _log(self, line: str) -> None:
        """Добавить строку в лог (thread-safe через after)."""
        self.after(0, lambda: self._append_log(line))

    def _append_log(self, line: str) -> None:
        # Убрать ANSI escape-коды перед выводом в tkinter
        line = _ANSI_RE.sub("", line)
        if not line.strip():
            return
        self._log_text.config(state="normal")
        tag = "default"
        l = line.lower()
        if "[info]" in l or "info" in l:       tag = "info"
        elif "[ok]" in l or "success" in l or "✓" in l or "ok:" in l or "╔" in line or "║" in line or "╚" in line or "╠" in line:
            tag = "ok"
        elif "[warn]" in l or "warn" in l:     tag = "warn"
        elif "[err]" in l or "error" in l or "failed" in l or "✗" in l: tag = "err"
        self._log_text.insert("end", line + "\n", tag)
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _start_install(self) -> None:
        if not os.path.exists(DEPLOY_ZIP_PATH):
            messagebox.showerror(
                "Пакет не найден",
                "Файл deploy.zip не найден.\n\nЗапустите build.bat для создания пакета установки.",
            )
            self._go_back()
            return

        self._btn_back.config(state="disabled")
        self._btn_next.config(state="disabled")
        self._progress.start(12)

        threading.Thread(target=self._install_thread, daemon=True).start()

    def _install_thread(self) -> None:
        sess = SSHSession(
            host=self.host.get().strip(),
            port=int(self.port.get()),
            username=self.username.get().strip(),
            password=self.password.get(),
        )
        password = self.password.get()

        def set_status(msg: str, color=COLORS["overlay"]) -> None:
            self.after(0, lambda: self._install_status_lbl.config(text=msg, fg=color))

        try:
            # 1. Подключение
            set_status("Подключение...")
            self._log(f"[INFO] Подключение к {sess.host}:{sess.port}...")
            sess.connect()
            self._log(f"[OK]   Подключён как {sess.username}")

            # 2. Определить: root или нет
            set_status("Проверка прав...")
            _, uid_out, _ = sess._client.exec_command("id -u")  # type: ignore[union-attr]
            uid = uid_out.read().decode().strip()
            is_root = (uid == "0")

            if is_root:
                self._log("[INFO] Работаем как root — sudo не нужен")
                sudo_prefix = ""
                sudo_stdin  = b""
            else:
                self._log(f"[INFO] Пользователь не root (uid={uid}), будет использован sudo -S")
                sudo_prefix = "sudo -S "
                sudo_stdin  = (password + "\n").encode()

            # 3. Загрузить deploy.zip
            set_status("Загрузка пакета...")
            self._log("[INFO] Загрузка пакета установки на сервер...")
            sess.upload(DEPLOY_ZIP_PATH, "/tmp/adtoolkit-deploy.zip", self._log)

            # 4. Загрузить / извлечь setup_server.sh
            setup_sh = os.path.join(BUNDLE_DIR, "setup_server.sh")
            if os.path.exists(setup_sh):
                sess.upload(setup_sh, "/tmp/adtoolkit-setup.sh", self._log)
            else:
                self._log("[INFO] Извлекаем setup_server.sh из архива...")
                with zipfile.ZipFile(DEPLOY_ZIP_PATH, "r") as zf:
                    names = [n for n in zf.namelist() if "setup_server.sh" in n]
                    if not names:
                        raise RuntimeError("setup_server.sh не найден в deploy.zip")
                    data = zf.read(names[0])
                sftp = sess._client.open_sftp()  # type: ignore[union-attr]
                with sftp.file("/tmp/adtoolkit-setup.sh", "w") as rf:
                    rf.write(data.decode())
                sftp.close()
                self._log("[OK]   setup_server.sh загружен")

            # 5. Права на скрипт
            sess.run(f"{sudo_prefix}chmod +x /tmp/adtoolkit-setup.sh",
                     self._log, stdin_data=sudo_stdin)

            # 6. Запустить установку
            set_status("Установка...")
            self._log("[INFO] Запуск сценария установки на сервере...")
            self._log("─" * 60)

            run_cmd = f"{sudo_prefix}bash /tmp/adtoolkit-setup.sh"
            rc = sess.run(run_cmd, self._log, stdin_data=sudo_stdin)

            self._log("─" * 60)

            if rc == 0:
                self._log(f"[OK]   Установка завершена успешно (exit {rc})")
                set_status("Успешно!", COLORS["green"])
                self.after(0, self._install_success)
            else:
                self._log(f"[ERR]  Скрипт завершился с ошибкой (exit {rc})")
                set_status(f"Ошибка (exit {rc})", COLORS["red"])
                self.after(0, self._install_failed)

        except Exception as ex:
            self._log(f"[ERR]  {ex}")
            set_status("Ошибка", COLORS["red"])
            self.after(0, self._install_failed)
        finally:
            sess.close()

    def _install_success(self) -> None:
        self._progress.stop()
        self._progress.config(mode="determinate", value=100)
        self._btn_next.config(state="normal", text="Готово →",
                              bg=COLORS["green"], command=lambda: self._show_page("done"))
        self._btn_back.config(state="disabled")

    def _install_failed(self) -> None:
        self._progress.stop()
        self._btn_back.config(state="normal")
        # Кнопка "Повторить" в футере
        self._btn_next.config(
            text="↺  Повторить",
            state="normal",
            bg=COLORS["yellow"],
            command=self._retry_install,
        )

    def _retry_install(self) -> None:
        """Сбросить лог и запустить установку повторно."""
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")
        self._btn_next.config(state="disabled", text="Далее →", bg=COLORS["btn_dis"])
        self._start_install()

    # ══════════════════════════════════════════════════════════════════════════
    #  Page: Done
    # ══════════════════════════════════════════════════════════════════════════

    def _page_done(self) -> None:
        f   = self._content
        url = f"http://{self.host.get().strip()}"
        if self.web_port.get() != "80":
            url += f":{self.web_port.get()}"

        tk.Label(f, text="✓  Установка завершена!",
                 font=("Segoe UI", 16, "bold"),
                 bg=COLORS["bg"], fg=COLORS["green"]).pack(pady=(30, 12))

        tk.Label(f, text="ADToolKit успешно установлен и запущен на сервере.",
                 font=("Segoe UI", 10), bg=COLORS["bg"], fg=COLORS["subtext"]).pack()

        # URL box
        url_frame = tk.Frame(f, bg=COLORS["panel"], padx=20, pady=14)
        url_frame.pack(pady=24)
        tk.Label(url_frame, text="Адрес веб-интерфейса:",
                 font=("Segoe UI", 9), bg=COLORS["panel"], fg=COLORS["subtext"]).pack()
        tk.Label(url_frame, text=url,
                 font=("Consolas", 14, "bold"),
                 bg=COLORS["panel"], fg=COLORS["accent"],
                 cursor="hand2").pack()

        # Open in browser
        def open_browser():
            import webbrowser
            webbrowser.open(url)

        btn = self._make_btn(f, "🌐  Открыть в браузере", open_browser)
        btn.pack(pady=4)

        # Log tips
        tk.Label(f, text=(
            "Полезные команды на сервере:\n"
            f"  journalctl -u adtoolkit-backend -f   # логи бэкенда\n"
            f"  systemctl status adtoolkit-backend    # статус сервиса\n"
            f"  systemctl restart adtoolkit-backend   # перезапуск"
        ), font=("Consolas", 8), bg=COLORS["bg"], fg=COLORS["overlay"],
            justify="left").pack(pady=(20, 0), anchor="w", padx=20)


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = InstallerApp()
    app.mainloop()
