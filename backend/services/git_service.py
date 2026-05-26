"""
git_service.py — git operations on the config-store/ directory.

All writes to config-store/ must be followed by a git commit so the
history is auditable and rollbacks are possible.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent / "iva-mail-ansible"


def _store_rel(path: Path) -> str:
    """Return path relative to repo root (for git commands)."""
    try:
        return str(path.relative_to(_repo_root()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Async subprocess helper
# ---------------------------------------------------------------------------

async def _run_git(*args: str, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr)."""
    cwd = cwd or _repo_root()
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


# ---------------------------------------------------------------------------
# Status / log
# ---------------------------------------------------------------------------

async def status() -> dict[str, Any]:
    """Return git status of the config-store/ subtree."""
    rc, out, err = await _run_git("status", "--short", "config-store/")
    return {
        "ok": rc == 0,
        "output": out.strip(),
        "error": err.strip() if rc != 0 else "",
    }


async def log(
    path: str | Path | None = None,
    max_count: int = 50,
) -> list[dict[str, Any]]:
    """
    Return git log entries.
    If path is given, restrict to commits touching that path.
    """
    args = [
        "log",
        f"--max-count={max_count}",
        "--pretty=format:%H|%an|%ae|%ai|%s",
    ]
    if path:
        args += ["--", str(path)]
    else:
        args += ["--", "config-store/"]

    rc, out, err = await _run_git(*args)
    if rc != 0:
        logger.warning("git log failed: %s", err)
        return []

    entries = []
    for line in out.strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        entries.append({
            "hash": parts[0],
            "author": parts[1],
            "email": parts[2],
            "date": parts[3],
            "message": parts[4],
        })
    return entries


async def show_diff(commit_hash: str, path: str | Path | None = None) -> str:
    """Return unified diff for a specific commit."""
    args = ["show", "--stat", commit_hash]
    if path:
        args += ["--", str(path)]
    rc, out, err = await _run_git(*args)
    return out if rc == 0 else f"Error: {err}"


async def diff_working_tree(path: str | Path | None = None) -> str:
    """Return diff of uncommitted changes in config-store/."""
    args = ["diff", "HEAD"]
    if path:
        args += ["--", str(path)]
    else:
        args += ["--", "config-store/"]
    rc, out, _ = await _run_git(*args)
    return out


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

async def commit_config_changes(
    message: str,
    author_name: str = "ADToolKit",
    author_email: str = "adtoolkit@local",
) -> dict[str, Any]:
    """
    Stage all changes in config-store/ and create a commit.
    Returns {ok, hash, message, error}.
    """
    # Stage
    rc_add, _, err_add = await _run_git("add", "config-store/")
    if rc_add != 0:
        return {"ok": False, "error": f"git add failed: {err_add}"}

    # Check if anything staged
    rc_st, st_out, _ = await _run_git("diff", "--cached", "--name-only", "config-store/")
    if not st_out.strip():
        return {"ok": True, "hash": None, "message": "Nothing to commit"}

    env_extra = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }

    import os
    merged_env = {**os.environ, **env_extra}
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", message,
        cwd=str(_repo_root()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    if proc.returncode != 0:
        return {"ok": False, "error": err.strip()}

    # Get the new commit hash
    _, hash_out, _ = await _run_git("rev-parse", "HEAD")
    return {
        "ok": True,
        "hash": hash_out.strip(),
        "message": message,
        "output": out.strip(),
    }


# ---------------------------------------------------------------------------
# Rollback / checkout
# ---------------------------------------------------------------------------

async def checkout_file_at_commit(
    file_path: str | Path,
    commit_hash: str,
) -> dict[str, Any]:
    """
    Restore a single file to its state at a given commit.
    This MODIFIES the working tree — caller must commit after applying.
    """
    rel = _store_rel(Path(file_path)) if Path(file_path).is_absolute() else str(file_path)
    rc, out, err = await _run_git("checkout", commit_hash, "--", rel)
    return {
        "ok": rc == 0,
        "path": rel,
        "commit": commit_hash,
        "error": err.strip() if rc != 0 else "",
    }


async def rollback_store_to_commit(commit_hash: str) -> dict[str, Any]:
    """
    Roll back the entire config-store/ to a given commit.
    Uses `git checkout <hash> -- config-store/`.
    """
    rc, out, err = await _run_git("checkout", commit_hash, "--", "config-store/")
    if rc != 0:
        return {"ok": False, "error": err.strip()}
    # Auto-commit the rollback
    result = await commit_config_changes(
        f"rollback: restore config-store to {commit_hash[:8]}"
    )
    return {**result, "rolled_back_to": commit_hash}


# ---------------------------------------------------------------------------
# File history
# ---------------------------------------------------------------------------

async def file_history(rel_path: str, max_count: int = 20) -> list[dict[str, Any]]:
    """Return commit history for a specific config-store file."""
    return await log(path=rel_path, max_count=max_count)
