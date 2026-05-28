#!/usr/bin/env python3
"""
deploy-backend.py -- Deploy ADToolKit backend to production server via SSH.

Deploys:
  - backend/          -> /opt/adtoolkit/backend/
  - iva-mail-ansible/ -> /opt/adtoolkit/iva-mail-ansible/

After upload: pip install -r requirements.txt + systemctl restart adtoolkit-backend

Usage:
  python deploy/deploy-backend.py
  python deploy/deploy-backend.py --host 10.3.6.100 --user adtoolkit

Credentials: SSH key from ~/.ssh/id_rsa or --key-file argument.
"""

import argparse
import os
import sys
import stat
import getpass
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_HOST  = "10.3.6.100"
DEFAULT_USER  = "user"
DEFAULT_PASS  = "DefaultP4ss"
DEFAULT_PORT  = 22
APP_DIR       = "/opt/adtoolkit"
SERVICE_NAME  = "adtoolkit-backend"

# Directories to sync (local relative path -> remote absolute path)
SYNC_DIRS = [
    ("backend",          f"{APP_DIR}/backend"),
    ("iva-mail-ansible", f"{APP_DIR}/iva-mail-ansible"),
]

# File extensions to upload (None = all files)
INCLUDE_EXTS = {".py", ".yml", ".yaml", ".j2", ".cfg", ".conf", ".txt", ".sh", ".md"}
# Dirs to skip entirely
SKIP_DIRS    = {"__pycache__", ".git", "node_modules", ".pytest_cache", "venv", ".venv", "_generated"}
# Files to skip
SKIP_FILES   = {".DS_Store", "*.pyc", "*.pyo"}


def log(msg: str) -> None:
    sys.stdout.buffer.write((msg + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def ok(msg: str) -> None:
    log(f"  [OK] {msg}")


def info(msg: str) -> None:
    log(f"  ... {msg}")


def error(msg: str) -> None:
    sys.stderr.buffer.write((f"  [ERR] {msg}\n").encode("utf-8"))
    sys.stderr.buffer.flush()


# ---------------------------------------------------------------------------
# SFTP helpers
# ---------------------------------------------------------------------------

def _should_skip(name: str, is_dir: bool) -> bool:
    if is_dir and name in SKIP_DIRS:
        return True
    if not is_dir:
        if name in SKIP_FILES:
            return True
        if INCLUDE_EXTS:
            ext = os.path.splitext(name)[1].lower()
            if ext not in INCLUDE_EXTS:
                return True
    return False


def _sftp_makedirs(sftp, remote_path: str) -> None:
    """Recursively create remote directories."""
    parts = remote_path.split("/")
    current = ""
    for part in parts:
        if not part:
            current = "/"
            continue
        current = current.rstrip("/") + "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            try:
                sftp.mkdir(current)
            except Exception:
                pass


def _upload_dir(sftp, local_dir: Path, remote_dir: str) -> tuple[int, int]:
    """Recursively upload local_dir to remote_dir. Returns (files_ok, files_skip)."""
    _sftp_makedirs(sftp, remote_dir)
    ok_count = 0
    skip_count = 0

    for entry in sorted(local_dir.iterdir()):
        if _should_skip(entry.name, entry.is_dir()):
            skip_count += 1
            continue

        remote_path = f"{remote_dir.rstrip('/')}/{entry.name}"

        if entry.is_dir():
            sub_ok, sub_skip = _upload_dir(sftp, entry, remote_path)
            ok_count += sub_ok
            skip_count += sub_skip
        elif entry.is_file():
            try:
                sftp.put(str(entry), remote_path)
                ok_count += 1
                info(f"uploaded: {entry.relative_to(local_dir.parent.parent)}")
            except Exception as exc:
                error(f"failed to upload {entry}: {exc}")

    return ok_count, skip_count


# ---------------------------------------------------------------------------
# SSH command helper
# ---------------------------------------------------------------------------

def run_remote(ssh, cmd: str, desc: str = "", sudo_pass: str = "") -> tuple[int, str]:
    """Execute a remote command, return (exit_code, stdout+stderr)."""
    if sudo_pass and "sudo" in cmd:
        # pipe password to sudo -S
        stripped = cmd.replace("sudo ", "")
        cmd = f"echo '{sudo_pass}' | sudo -S sh -c '{stripped}'"

    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    rc  = stdout.channel.recv_exit_status()

    combined = "\n".join(filter(None, [out, err]))
    if rc == 0:
        if desc:
            ok(desc)
        if combined:
            info(combined[:300])
    else:
        error(f"{desc or cmd}: exit {rc}")
        if combined:
            error(combined[:500])

    return rc, combined


# ---------------------------------------------------------------------------
# Main deploy logic
# ---------------------------------------------------------------------------

def deploy(args: argparse.Namespace) -> int:
    try:
        import paramiko
    except ImportError:
        error("paramiko is not installed. Run: pip install paramiko")
        return 1

    # Locate project root (parent of this script)
    script_dir  = Path(__file__).resolve().parent
    project_root = script_dir.parent
    log(f"\n=== ADToolKit Backend Deploy ===")
    log(f"Project:  {project_root}")
    log(f"Target:   {args.user}@{args.host}:{args.port}")

    # Connect
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = dict(
            hostname=args.host,
            port=args.port,
            username=args.user,
            timeout=15,
        )
        if args.key_file:
            connect_kwargs["key_filename"] = args.key_file
        elif args.password:
            connect_kwargs["password"] = args.password
        else:
            # Try default key locations
            for key_path in [
                os.path.expanduser("~/.ssh/id_rsa"),
                os.path.expanduser("~/.ssh/id_ed25519"),
            ]:
                if os.path.exists(key_path):
                    connect_kwargs["key_filename"] = key_path
                    break

        log(f"\n[1/4] Connecting...")
        ssh.connect(**connect_kwargs)
        ok(f"Connected to {args.host}")
    except Exception as exc:
        error(f"SSH connection failed: {exc}")
        return 1

    sftp   = ssh.open_sftp()
    sudo_p = args.sudo_password or args.password or ""
    REMOTE_TMP = "/tmp/adt-backend-deploy"

    # Step 2: Upload to /tmp first (user has write permission there)
    total_ok = 0
    total_skip = 0
    log(f"\n[2/4] Uploading files to {REMOTE_TMP}/ ...")
    # Clean tmp dir
    run_remote(ssh, f"rm -rf {REMOTE_TMP} && mkdir -p {REMOTE_TMP}")

    for local_rel, _remote_abs in SYNC_DIRS:
        local_path = project_root / local_rel
        if not local_path.is_dir():
            error(f"Local directory not found: {local_path}")
            sftp.close()
            ssh.close()
            return 1
        remote_tmp_sub = f"{REMOTE_TMP}/{local_rel}"
        info(f"Uploading {local_rel}/ -> {remote_tmp_sub}/")
        f_ok, f_skip = _upload_dir(sftp, local_path, remote_tmp_sub)
        total_ok   += f_ok
        total_skip += f_skip
        ok(f"{local_rel}: {f_ok} files, {f_skip} skipped")

    sftp.close()

    # Move from /tmp to final location with sudo
    log(f"\n[2b/4] Installing files to {APP_DIR}/ (sudo cp) ...")
    for local_rel, remote_abs in SYNC_DIRS:
        cp_cmd = (
            f"echo '{sudo_p}' | sudo -S cp -r {REMOTE_TMP}/{local_rel}/. {remote_abs}/ && "
            f"echo '{sudo_p}' | sudo -S chown -R adtoolkit:adtoolkit {remote_abs}/"
        )
        rc, _ = run_remote(ssh, cp_cmd, f"installed {local_rel}/")
        if rc != 0:
            error(f"Failed to install {local_rel}")

    # Cleanup tmp
    run_remote(ssh, f"rm -rf {REMOTE_TMP}")

    # Step 2c: ensure config-store dir exists and is owned by adtoolkit
    CONFIG_STORE = "/opt/ivamail-config-store"
    log(f"\n[2c/4] Ensuring {CONFIG_STORE}/ exists ...")
    cs_cmd = (
        f"echo '{sudo_p}' | sudo -S bash -c '"
        f"mkdir -p {CONFIG_STORE} && "
        f"chown adtoolkit:adtoolkit {CONFIG_STORE} && "
        f"chmod 750 {CONFIG_STORE}'"
    )
    rc, _ = run_remote(ssh, cs_cmd, f"{CONFIG_STORE} ready")
    if rc != 0:
        error(f"Failed to prepare {CONFIG_STORE}")

    # Init git repo if not yet initialised (idempotent)
    git_init_cmd = (
        f"echo '{sudo_p}' | sudo -S -u adtoolkit bash -c '"
        f"cd {CONFIG_STORE} && "
        f"git rev-parse --git-dir > /dev/null 2>&1 || ("
        f"git init -q && "
        f"git config user.email adtoolkit@local && "
        f"git config user.name \"ADToolKit Ansible\" && "
        f"touch .gitkeep && git add .gitkeep && "
        f"git commit -q -m \"chore: init ivamail config-store\")'"
    )
    run_remote(ssh, git_init_cmd, "git repo initialised")

    # Step 3: pip install (as adtoolkit user via sudo)
    log(f"\n[3/4] Installing Python dependencies...")
    pip_cmd = (
        f"echo '{sudo_p}' | sudo -S -u adtoolkit "
        f"{APP_DIR}/venv/bin/pip install -q -r {APP_DIR}/backend/requirements.txt"
    )
    rc, _ = run_remote(ssh, pip_cmd, "pip install -r requirements.txt")
    if rc != 0:
        error("pip install failed — check requirements.txt")

    # Step 4: Restart service (sudo required)
    log(f"\n[4/4] Restarting {SERVICE_NAME}...")
    restart_cmd = f"echo '{sudo_p}' | sudo -S systemctl restart {SERVICE_NAME}"
    rc, out = run_remote(ssh, restart_cmd, f"{SERVICE_NAME} restarted")
    if rc != 0:
        error(f"Failed to restart {SERVICE_NAME}")
        # Show recent logs
        run_remote(ssh, f"journalctl -u {SERVICE_NAME} --no-pager -n 30", "recent logs")
        ssh.close()
        return 1

    # Quick health check — give the service time to start
    import time
    time.sleep(6)
    rc, out = run_remote(
        ssh,
        "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/deployment/status",
        "API health check",
    )
    http_code = out.strip()
    if http_code in ("200", "404", "422"):
        ok(f"API is up (HTTP {http_code})")
    else:
        error(f"API health check returned: {http_code!r}")

    ssh.close()

    log(f"\n=== Deploy complete ===")
    log(f"  Files uploaded: {total_ok}")
    log(f"  Files skipped:  {total_skip}")
    log(f"  Service: {SERVICE_NAME} restarted")
    log(f"  Logs: journalctl -u {SERVICE_NAME} -f\n")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy ADToolKit backend to production server",
    )
    parser.add_argument("--host",    default=DEFAULT_HOST, help=f"Server IP (default: {DEFAULT_HOST})")
    parser.add_argument("--port",    type=int, default=DEFAULT_PORT, help=f"SSH port (default: {DEFAULT_PORT})")
    parser.add_argument("--user",    default=DEFAULT_USER, help=f"SSH user (default: {DEFAULT_USER})")
    parser.add_argument("--key-file", dest="key_file", default=None, help="Path to SSH private key")
    parser.add_argument("--password", default=DEFAULT_PASS, help=f"SSH password (default: {DEFAULT_PASS})")
    parser.add_argument("--sudo-password", dest="sudo_password", default=DEFAULT_PASS,
                        help="sudo password for systemctl restart (default: same as --password)")
    args = parser.parse_args()

    sys.exit(deploy(args))


if __name__ == "__main__":
    main()
