#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy-frontend.py - build & deploy React SPA to ADToolKit server.

Usage:
    python deploy/deploy-frontend.py [--no-build] [--host HOST] [--user USER]

Steps:
  1. npm run build  (inside frontend/)
  2. SFTP: upload frontend/dist/ -> /tmp/adt-dist-deploy/ on server
  3. sudo cp /tmp/adt-dist-deploy/* -> /opt/adtoolkit/frontend/dist/  (nginx root)
  4. cleanup /tmp/adt-dist-deploy/

nginx root: /opt/adtoolkit/frontend/dist/
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# ── config ─────────────────────────────────────────────────────────────────────

DEFAULT_HOST = "10.3.6.100"
DEFAULT_USER = "user"
DEFAULT_PASS = "DefaultP4ss"
REMOTE_ROOT  = "/opt/adtoolkit/frontend/dist"
REMOTE_TMP   = "/tmp/adt-dist-deploy"

REPO_ROOT    = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
LOCAL_DIST   = FRONTEND_DIR / "dist"

# ── helpers ────────────────────────────────────────────────────────────────────

def info(msg: str) -> None:
    sys.stdout.buffer.write((msg + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def run(cmd: list, cwd: Path | None = None) -> None:
    pretty = " ".join(str(c) for c in cmd)
    info(f"\n>>> {pretty}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        info(f"[ERROR] command exited with code {result.returncode}")
        sys.exit(result.returncode)


def sftp_upload_dir(sftp, local_dir: Path, remote_dir: str) -> int:
    count = 0
    for item in sorted(local_dir.rglob("*")):
        if item.is_file():
            rel = item.relative_to(local_dir)
            remote_path = f"{remote_dir}/{str(rel).replace(os.sep, '/')}"
            parent = remote_path.rsplit("/", 1)[0]
            _sftp_makedirs(sftp, parent)
            sftp.put(str(item), remote_path)
            count += 1
            info(f"  upload: {rel}")
    return count


def _sftp_makedirs(sftp, path: str) -> None:
    parts = path.lstrip("/").split("/")
    current = ""
    for part in parts:
        current += f"/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            try:
                sftp.mkdir(current)
            except OSError:
                pass


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy ADToolKit frontend")
    parser.add_argument("--no-build", action="store_true",
                        help="Skip build (use existing dist/)")
    parser.add_argument("--host",     default=DEFAULT_HOST)
    parser.add_argument("--user",     default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASS)
    args = parser.parse_args()

    # ── Step 1: Build ──────────────────────────────────────────────────────────
    if not args.no_build:
        info("\n[1/3] Building frontend...")
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        run([npm, "run", "build"], cwd=FRONTEND_DIR)
    else:
        info("\n[1/3] Build skipped (--no-build)")

    if not LOCAL_DIST.exists():
        info(f"[ERROR] {LOCAL_DIST} not found - run without --no-build")
        sys.exit(1)

    # ── Step 2: Upload via SFTP ────────────────────────────────────────────────
    info(f"\n[2/3] Uploading to {args.host} -> {REMOTE_TMP}/")

    try:
        import paramiko  # noqa: PLC0415
    except ImportError:
        info("[ERROR] paramiko not installed: pip install paramiko")
        sys.exit(1)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, username=args.user, password=args.password, timeout=15)

    # Clean and recreate remote tmp dir
    _, out, err = client.exec_command(f"rm -rf {REMOTE_TMP} && mkdir -p {REMOTE_TMP}", timeout=15)
    out.read(); err.read()

    sftp = client.open_sftp()
    n = sftp_upload_dir(sftp, LOCAL_DIST, REMOTE_TMP)
    sftp.close()
    info(f"  Uploaded {n} files")

    # ── Step 3: Replace nginx root (sudo) ──────────────────────────────────────
    info(f"\n[3/3] Installing to nginx root: {REMOTE_ROOT}/")

    # Use sudo -S to read password from stdin
    copy_cmd = f"echo '{args.password}' | sudo -S cp -r {REMOTE_TMP}/. {REMOTE_ROOT}/"
    _, out, err = client.exec_command(copy_cmd, timeout=30)
    out.read(); err.read()  # drain

    # Verify
    _, out, _ = client.exec_command(f"ls -la {REMOTE_ROOT}/assets/ 2>/dev/null", timeout=10)
    listing = out.read().decode("utf-8", errors="replace").strip()
    if listing:
        info(f"  OK - {REMOTE_ROOT}/assets/ contents:")
        for line in listing.splitlines():
            info(f"    {line}")
    else:
        info(f"  [ERROR] {REMOTE_ROOT}/assets/ is empty or missing")
        sys.exit(1)

    # Cleanup tmp
    _, out, err = client.exec_command(f"rm -rf {REMOTE_TMP}", timeout=10)
    out.read(); err.read()

    client.close()
    info(f"\nDeploy complete. Open http://{args.host}/ in browser.")


if __name__ == "__main__":
    main()
