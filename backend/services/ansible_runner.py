"""
ansible_runner.py — async wrapper for running Ansible playbooks.

Streams stdout/stderr lines via an async generator.
Used by config management API to run dump/apply/rollback playbooks.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _ansible_dir() -> Path:
    return Path(__file__).parent.parent.parent / "iva-mail-ansible"


# ---------------------------------------------------------------------------
# Playbook runner
# ---------------------------------------------------------------------------

class PlaybookResult:
    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        playbook: str,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.playbook = playbook

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "playbook": self.playbook,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


async def run_playbook(
    playbook: str,
    extra_vars: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    limit: str | None = None,
    inventory: str | None = None,
    vault_password_file: str | None = None,
    timeout: int = 3600,
) -> PlaybookResult:
    """
    Run an ansible-playbook and collect full output.
    Returns PlaybookResult with ok/stdout/stderr.
    """
    cmd = _build_cmd(playbook, extra_vars, tags, limit, inventory, vault_password_file)
    logger.info("Running playbook: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_ansible_dir()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return PlaybookResult(-1, "", f"Timeout after {timeout}s", playbook)

    return PlaybookResult(
        returncode=proc.returncode,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
        playbook=playbook,
    )


async def stream_playbook(
    playbook: str,
    extra_vars: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    limit: str | None = None,
    inventory: str | None = None,
    vault_password_file: str | None = None,
    timeout: int = 3600,
) -> AsyncGenerator[str, None]:
    """
    Run an ansible-playbook and yield output lines as they arrive.
    Useful for SSE streaming of playbook progress.
    """
    cmd = _build_cmd(playbook, extra_vars, tags, limit, inventory, vault_password_file)
    logger.info("Streaming playbook: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_ansible_dir()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout for streaming
    )

    assert proc.stdout is not None
    deadline = asyncio.get_event_loop().time() + timeout

    try:
        async for line_b in proc.stdout:
            if asyncio.get_event_loop().time() > deadline:
                proc.kill()
                yield "[TIMEOUT] Playbook exceeded max runtime"
                break
            yield line_b.decode(errors="replace").rstrip()
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()

    rc = proc.returncode or 0
    if rc != 0:
        yield f"[EXIT {rc}] Playbook finished with errors"
    else:
        yield "[EXIT 0] Playbook completed successfully"


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------

def _build_cmd(
    playbook: str,
    extra_vars: dict[str, Any] | None,
    tags: list[str] | None,
    limit: str | None,
    inventory: str | None,
    vault_password_file: str | None,
) -> list[str]:
    cmd = ["ansible-playbook", playbook]

    if inventory:
        cmd += ["-i", inventory]

    if extra_vars:
        import json
        cmd += ["--extra-vars", json.dumps(extra_vars)]

    if tags:
        cmd += ["--tags", ",".join(tags)]

    if limit:
        cmd += ["--limit", limit]

    if vault_password_file:
        cmd += ["--vault-password-file", vault_password_file]

    return cmd


# ---------------------------------------------------------------------------
# Well-known playbook shortcuts
# ---------------------------------------------------------------------------

PLAYBOOKS = {
    "config_dump":     "playbooks/07-config-dump.yml",
    "config_apply":    "playbooks/08-config-apply.yml",
    "config_rollback": "playbooks/09-config-rollback.yml",
}


async def run_config_dump(
    hosts: list[str] | None = None,
    extra_vars: dict[str, Any] | None = None,
) -> PlaybookResult:
    """Run the config dump playbook (read from nodes → save to config-store/)."""
    ev = extra_vars or {}
    if hosts:
        ev["backend_hosts"] = ",".join(hosts)
    return await run_playbook(PLAYBOOKS["config_dump"], extra_vars=ev)


async def run_config_apply(
    hosts: list[str] | None = None,
    extra_vars: dict[str, Any] | None = None,
) -> PlaybookResult:
    """Run the config apply playbook (config-store/ → push to nodes via Ansible)."""
    ev = extra_vars or {}
    if hosts:
        ev["backend_hosts"] = ",".join(hosts)
    return await run_playbook(PLAYBOOKS["config_apply"], extra_vars=ev)


async def stream_config_dump(
    hosts: list[str] | None = None,
    extra_vars: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    ev = extra_vars or {}
    if hosts:
        ev["backend_hosts"] = ",".join(hosts)
    return stream_playbook(PLAYBOOKS["config_dump"], extra_vars=ev)


async def stream_config_apply(
    hosts: list[str] | None = None,
    extra_vars: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    ev = extra_vars or {}
    if hosts:
        ev["backend_hosts"] = ",".join(hosts)
    return stream_playbook(PLAYBOOKS["config_apply"], extra_vars=ev)
