"""
api/config_store.py — Config Management REST API.

Variant B flow:
  Live Read  → CMD (read-only, per-node)
  Save       → YAML file in config-store/ + git commit
  Apply      → ansible-playbook 12-config-apply.yml
  Rollback   → git checkout + ansible-playbook 13-config-rollback.yml

Endpoints
---------
Live read (CMD, read-only):
  GET  /live/nodes/{ip}/modules              — list modules on a node
  GET  /live/nodes/{ip}/modules/{module}     — read one module config from live node
  GET  /live/domains                         — list domains from live node
  GET  /live/domains/{domain}                — read domain config from live node
  GET  /live/domains/{domain}/objects        — list objects in domain
  GET  /live/domains/{domain}/objects/{uid}  — read one object config
  GET  /live/schema                          — read schema dump from first backend

Stored config (config-store/ YAML):
  GET  /stored/nodes                         — list nodes that have stored configs
  GET  /stored/nodes/{ip}/modules            — list stored module names
  GET  /stored/nodes/{ip}/modules/{module}   — read stored module config
  GET  /stored/domains                       — list stored domains
  GET  /stored/domains/{domain}              — read stored domain config
  GET  /stored/domains/{domain}/objects      — list stored objects
  GET  /stored/domains/{domain}/objects/{uid} — read stored object config

  POST /stored/save/module                   — save live module → YAML + git commit
  POST /stored/save/domain                   — save live domain → YAML + git commit
  POST /stored/save/schema                   — save live schema → YAML + git commit

Diff:
  GET  /diff/module?ip=&module=              — compare stored vs live
  GET  /diff/domain?domain=                  — compare stored vs live

Git:
  GET  /git/log                              — recent commits touching config-store/
  GET  /git/log/file?path=                   — history for a specific file
  POST /git/rollback                         — rollback store to a commit hash

Ansible:
  POST /ansible/dump                         — run 11-config-dump.yml (stores live → YAML)
  POST /ansible/apply                        — run 12-config-apply.yml (YAML → nodes)
  GET  /ansible/stream/{job_id}              — SSE stream of a running playbook
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..infrastructure.cmd_client import CMDClient
from ..services import config_store as cs
from ..services import git_service as git
from ..services import ansible_runner as ar

# Путь к репозиторию config-store (переопределяется через env CONFIG_STORE_DIR)
_DEFAULT_CONFIG_STORE = "/opt/ivamail-config-store"
CONFIG_STORE_DIR: str = os.environ.get("CONFIG_STORE_DIR", _DEFAULT_CONFIG_STORE)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["config"])

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SaveModuleRequest(BaseModel):
    ip: str
    port: int = 106
    module: str
    cmd_user: str
    cmd_password: str


class SaveDomainRequest(BaseModel):
    ip: str          # node to read from
    port: int = 106
    domain: str
    cmd_user: str
    cmd_password: str


class SaveSchemaRequest(BaseModel):
    ip: str
    port: int = 106
    scope: Optional[str] = None
    cmd_user: str
    cmd_password: str


class GitRollbackRequest(BaseModel):
    commit_hash: str


class AnsibleRunRequest(BaseModel):
    hosts: Optional[list[str]] = None
    extra_vars: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# CMD helper
# ---------------------------------------------------------------------------

async def _cmd(ip: str, port: int, user: str, password: str) -> CMDClient:
    client = CMDClient(host=ip, port=port)
    try:
        await client.connect()
        await client.authenticate(user, password)
    except Exception as exc:
        await client.close()
        raise HTTPException(status_code=401, detail=f"CMD auth failed on {ip}: {exc}") from exc
    return client


def _parse_lines(raw: str) -> list[str]:
    return [line for line in raw.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# ── LIVE READ ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@router.get("/live/nodes/{ip}/modules")
async def live_list_modules(
    ip: str,
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
) -> dict[str, Any]:
    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.modules_list()
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])
    try:
        raw = json.loads(resp.text)
        modules = raw if isinstance(raw, list) else list(raw.values()) if isinstance(raw, dict) else []
    except Exception:
        modules = [l.strip() for l in resp.text.splitlines() if l.strip()]
    return {"ip": ip, "modules": modules}


@router.get("/live/nodes/{ip}/modules/{module}")
async def live_read_module(
    ip: str,
    module: str,
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
) -> dict[str, Any]:
    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.read_module_config(module)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])
    try:
        config = json.loads(resp.text)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}
    return {"ip": ip, "module": module, "config": config}


@router.get("/live/domains")
async def live_list_domains(
    ip: str = Query(...),
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
) -> dict[str, Any]:
    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.domains_list()
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])
    try:
        raw = json.loads(resp.text)
        # DOMAINSLIST возвращает {"/N 1": "domain.name", ...}
        if isinstance(raw, dict):
            domains = [v for v in raw.values() if isinstance(v, str)]
        elif isinstance(raw, list):
            domains = [str(x) for x in raw]
        else:
            domains = []
    except Exception:
        domains = [l.strip() for l in resp.text.splitlines() if l.strip()]
    return {"domains": domains}


@router.get("/live/domains/{domain}")
async def live_read_domain(
    domain: str,
    ip: str = Query(...),
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
) -> dict[str, Any]:
    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.domain_read_config(domain)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])
    try:
        config = json.loads(resp.text)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}
    return {"domain": domain, "config": config}


@router.get("/live/domains/{domain}/objects")
async def live_list_objects(
    domain: str,
    ip: str = Query(...),
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
    obj_type: Optional[str] = Query(None),
) -> dict[str, Any]:
    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.objects_list(domain, obj_type)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])
    try:
        raw = json.loads(resp.text)
        # OBJECTSLIST возвращает {"/N 1": {...}, ...} или [...]
        if isinstance(raw, dict):
            objects = list(raw.keys())
        elif isinstance(raw, list):
            objects = [str(x) for x in raw]
        else:
            objects = []
    except Exception:
        objects = [l.strip() for l in resp.text.splitlines() if l.strip()]
    return {"domain": domain, "objects": objects}


@router.get("/live/domains/{domain}/objects/{uid}")
async def live_read_object(
    domain: str,
    uid: str,
    ip: str = Query(...),
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
) -> dict[str, Any]:
    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.object_read_config(domain, uid)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])
    try:
        config = json.loads(resp.text)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}
    return {"domain": domain, "uid": uid, "config": config}


@router.get("/live/schema")
async def live_read_schema(
    ip: str = Query(...),
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
    scope: Optional[str] = Query(None),
) -> dict[str, Any]:
    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.schema_dump(scope)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])
    try:
        schema = json.loads(resp.text)
    except Exception:
        schema = {}
    return {"schema": schema}


# ---------------------------------------------------------------------------
# ── STORED CONFIG ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@router.get("/stored/nodes")
async def stored_list_nodes() -> dict[str, Any]:
    return {"nodes": cs.list_all_node_ips()}


@router.get("/stored/nodes/{ip}/modules")
async def stored_list_modules(ip: str) -> dict[str, Any]:
    return {"ip": ip, "modules": cs.list_module_configs(ip)}


@router.get("/stored/nodes/{ip}/modules/{module}")
async def stored_read_module(ip: str, module: str) -> dict[str, Any]:
    data = cs.load_module_config(ip, module)
    if not data:
        raise HTTPException(status_code=404, detail="No stored config found")
    return {"ip": ip, "module": module, "config": data}


@router.get("/stored/domains")
async def stored_list_domains() -> dict[str, Any]:
    return {"domains": cs.list_saved_domains()}


@router.get("/stored/domains/{domain}")
async def stored_read_domain(domain: str) -> dict[str, Any]:
    data = cs.load_domain_config(domain)
    if not data:
        raise HTTPException(status_code=404, detail="No stored config found")
    return {"domain": domain, "config": data}


@router.get("/stored/domains/{domain}/objects")
async def stored_list_objects(domain: str) -> dict[str, Any]:
    return {"domain": domain, "objects": cs.list_saved_objects(domain)}


@router.get("/stored/domains/{domain}/objects/{uid}")
async def stored_read_object(domain: str, uid: str) -> dict[str, Any]:
    data = cs.load_object_config(domain, uid)
    if not data:
        raise HTTPException(status_code=404, detail="No stored config found")
    return {"domain": domain, "uid": uid, "config": data}


# ---------------------------------------------------------------------------
# ── SAVE (live CMD → YAML + git commit) ───────────────────────────────────
# ---------------------------------------------------------------------------

@router.post("/stored/save/module")
async def save_module(req: SaveModuleRequest) -> dict[str, Any]:
    client = await _cmd(req.ip, req.port, req.cmd_user, req.cmd_password)
    resp = await client.read_module_config(req.module)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])

    try:
        config = json.loads(resp.text)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}
    path = await cs.save_module_config(req.ip, req.module, config, source="live_read")

    commit = await git.commit_config_changes(
        f"config: save module {req.module} from {req.ip}"
    )
    return {"saved": True, "path": str(path), "git": commit}


@router.post("/stored/save/domain")
async def save_domain(req: SaveDomainRequest) -> dict[str, Any]:
    client = await _cmd(req.ip, req.port, req.cmd_user, req.cmd_password)
    resp = await client.domain_read_config(req.domain)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])

    try:
        config = json.loads(resp.text)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}
    path = await cs.save_domain_config(req.domain, config, source="live_read")

    commit = await git.commit_config_changes(
        f"config: save domain {req.domain} from {req.ip}"
    )
    return {"saved": True, "path": str(path), "git": commit}


@router.post("/stored/save/schema")
async def save_schema(req: SaveSchemaRequest) -> dict[str, Any]:
    client = await _cmd(req.ip, req.port, req.cmd_user, req.cmd_password)
    resp = await client.schema_dump(req.scope)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])

    try:
        schema_data = json.loads(resp.text)
    except Exception:
        schema_data = {}

    path = await cs.save_schema(schema_data)
    commit = await git.commit_config_changes("config: save schema dump")
    return {"saved": True, "path": str(path), "git": commit}


# ---------------------------------------------------------------------------
# ── DIFF ──────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@router.get("/diff/module")
async def diff_module(
    ip: str = Query(...),
    module: str = Query(...),
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
) -> dict[str, Any]:
    stored = cs.load_module_config(ip, module)

    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.read_module_config(module)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])

    try:
        live = json.loads(resp.text)
        if not isinstance(live, dict):
            live = {}
    except Exception:
        live = {}
    diff = cs.diff_configs(stored, live)
    return {"ip": ip, "module": module, "diff": diff}


@router.get("/diff/domain")
async def diff_domain(
    domain: str = Query(...),
    ip: str = Query(...),
    port: int = Query(106),
    cmd_user: str = Query(...),
    cmd_password: str = Query(...),
) -> dict[str, Any]:
    stored = cs.load_domain_config(domain)

    client = await _cmd(ip, port, cmd_user, cmd_password)
    resp = await client.domain_read_config(domain)
    await client.close()
    if not resp.ok:
        raise HTTPException(status_code=502, detail=resp.text[:200])

    try:
        live = json.loads(resp.text)
        if not isinstance(live, dict):
            live = {}
    except Exception:
        live = {}
    diff = cs.diff_configs(stored, live)
    return {"domain": domain, "diff": diff}


# ---------------------------------------------------------------------------
# ── GIT ───────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

@router.get("/git/log")
async def git_log(max_count: int = Query(50, le=200)) -> dict[str, Any]:
    entries = await git.log(max_count=max_count)
    return {"commits": entries}


@router.get("/git/log/file")
async def git_file_log(
    path: str = Query(...),
    max_count: int = Query(20, le=100),
) -> dict[str, Any]:
    entries = await git.file_history(path, max_count=max_count)
    return {"path": path, "commits": entries}


@router.get("/git/diff/{commit_hash}")
async def git_show_diff(commit_hash: str) -> dict[str, Any]:
    diff = await git.show_diff(commit_hash)
    return {"commit": commit_hash, "diff": diff}


@router.post("/git/rollback")
async def git_rollback(req: GitRollbackRequest) -> dict[str, Any]:
    result = await git.rollback_store_to_commit(req.commit_hash)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Rollback failed"))
    return result


# ---------------------------------------------------------------------------
# ── ANSIBLE ───────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

# In-memory store for async playbook jobs (job_id → task)
_running_jobs: dict[str, asyncio.Task] = {}
_job_output: dict[str, list[str]] = {}
_job_status: dict[str, str] = {}   # "running" | "done" | "failed"


@router.post("/ansible/dump")
async def ansible_dump(req: AnsibleRunRequest) -> dict[str, Any]:
    """
    Run 11-config-dump.yml synchronously.
    Reads live configs from nodes and saves them to config-store/.
    """
    result = await ar.run_config_dump(hosts=req.hosts, extra_vars=req.extra_vars)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.stderr[:2000])
    return result.to_dict()


@router.post("/ansible/apply")
async def ansible_apply(req: AnsibleRunRequest) -> dict[str, Any]:
    """
    Run 12-config-apply.yml synchronously.
    Pushes config-store/ YAML values to nodes via Ansible.
    """
    result = await ar.run_config_apply(hosts=req.hosts, extra_vars=req.extra_vars)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.stderr[:2000])
    return result.to_dict()


@router.post("/ansible/dump/stream")
async def ansible_dump_stream(req: AnsibleRunRequest) -> StreamingResponse:
    """SSE stream of config dump playbook output."""
    async def _gen():
        gen = ar.stream_playbook(
            ar.PLAYBOOKS["config_dump"],
            extra_vars={**(req.extra_vars or {}), **({"backend_hosts": ",".join(req.hosts)} if req.hosts else {})},
        )
        async for line in gen:
            yield f"data: {line}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/ansible/apply/stream")
async def ansible_apply_stream(req: AnsibleRunRequest) -> StreamingResponse:
    """SSE stream of config apply playbook output."""
    async def _gen():
        gen = ar.stream_playbook(
            ar.PLAYBOOKS["config_apply"],
            extra_vars={**(req.extra_vars or {}), **({"backend_hosts": ",".join(req.hosts)} if req.hosts else {})},
        )
        async for line in gen:
            yield f"data: {line}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# ── НОВЫЕ ЭНДПОИНТЫ ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

# ── Request-модели ──────────────────────────────────────────────────────────

class DumpStreamRequest(BaseModel):
    """Запрос для запуска dump-плейбука с SSE-стримингом."""
    hosts: list[str]
    include_objects: bool = False
    config_tag_name: str = ""


class ApplyStreamRequest(BaseModel):
    """Запрос для генерации плейбука из config-store и его запуска."""
    hosts: list[str]
    mode: str = "full"           # "full" | "diff"
    include_objects: bool = False


class RollbackStreamRequest(BaseModel):
    """Запрос для отката config-store к git-тегу."""
    hosts: list[str]
    tag: str                     # имя git-тега (например "config-20240115T103000")
    mode: str = "yaml_only"      # "yaml_only" | "yaml_and_apply"


# ── 2.1: GET /api/config/git/tags ──────────────────────────────────────────

@router.get("/git/tags")
async def get_config_tags() -> dict[str, Any]:
    """
    Список git-тегов в config-store репозитории.
    Возвращает теги с префиксом 'config-', отсортированные от новых к старым.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", CONFIG_STORE_DIR,
            "tag", "--list", "config-*", "--sort=-creatordate",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError as exc:
        logger.error("git не найден или CONFIG_STORE_DIR недоступен: %s", exc)
        raise HTTPException(status_code=500, detail=f"git недоступен: {exc}") from exc

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        logger.error("git tag завершился с ошибкой: %s", err_msg)
        raise HTTPException(status_code=500, detail=f"git tag failed: {err_msg}")

    tags = [
        line.strip()
        for line in stdout.decode(errors="replace").splitlines()
        if line.strip()
    ]
    logger.info("Найдено %d тегов config-store", len(tags))
    return {"tags": tags}


# ── 2.2: POST /api/config/ansible/dump/stream (расширенный) ────────────────

@router.post("/ansible/dump/stream/v2")
async def ansible_dump_stream_v2(body: DumpStreamRequest) -> StreamingResponse:
    """
    SSE-стрим запуска 07-config-dump.yml с поддержкой include_objects и config_tag_name.
    Расширенная версия существующего /ansible/dump/stream.
    """
    extra_vars: dict[str, Any] = {
        "backend_hosts": ",".join(body.hosts),
        "include_objects": body.include_objects,
    }
    if body.config_tag_name:
        extra_vars["config_tag_name"] = body.config_tag_name

    async def _gen():
        gen = ar.stream_playbook(
            ar.PLAYBOOKS["config_dump"],
            extra_vars=extra_vars,
        )
        async for line in gen:
            yield f"data: {line}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 2.3: POST /api/config/ansible/apply/stream/v2 (генерация + запуск) ─────

@router.post("/ansible/apply/stream/v2")
async def ansible_apply_stream_v2(
    body: ApplyStreamRequest,
    background_tasks: BackgroundTasks,
) -> StreamingResponse:
    """
    Генерирует плейбук из config-store и запускает его с SSE-стримингом вывода.

    Шаги:
    1. Генерирует YAML-плейбук через playbook_generator
    2. Сохраняет в {CONFIG_STORE_DIR}/_generated/
    3. Запускает ansible-playbook и стримит вывод как text/event-stream
    """
    from ..services.playbook_generator import generate_apply_playbook, save_generated_playbook

    # Генерируем плейбук синхронно перед стримингом
    try:
        content = generate_apply_playbook(
            hosts=body.hosts,
            config_store_dir=CONFIG_STORE_DIR,
            mode=body.mode,
            include_objects=body.include_objects,
        )
        playbook_path = save_generated_playbook(content, CONFIG_STORE_DIR, "apply")
        logger.info("Сгенерирован плейбук: %s", playbook_path)
    except Exception as exc:
        logger.error("Ошибка генерации плейбука: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ошибка генерации плейбука: {exc}") from exc

    async def _gen():
        yield f"data: [GEN] Playbook generated: {playbook_path}\n\n"
        gen = ar.stream_playbook(
            playbook_path,
            extra_vars={"backend_hosts": ",".join(body.hosts)},
        )
        async for line in gen:
            yield f"data: {line}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 2.4: POST /api/config/ansible/rollback/stream ──────────────────────────

@router.post("/ansible/rollback/stream")
async def ansible_rollback_stream(body: RollbackStreamRequest) -> StreamingResponse:
    """
    Откат config-store к git-тегу с опциональным применением на ноды.

    Режимы:
      yaml_only     — git checkout тега (без применения на ноды)
      yaml_and_apply — git checkout + генерация плейбука + apply

    SSE-стрим вывода.
    """
    from ..services.playbook_generator import generate_apply_playbook, save_generated_playbook

    async def _gen():
        # Шаг 1: git checkout тега в config-store
        yield f"data: [ROLLBACK] Checking out tag: {body.tag}\n\n"

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", CONFIG_STORE_DIR,
                "checkout", body.tag, "--", ".",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError as exc:
            yield f"data: [ERROR] git не найден: {exc}\n\n"
            return

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error("git checkout %s завершился с ошибкой: %s", body.tag, err_msg)
            yield f"data: [ERROR] git checkout failed: {err_msg}\n\n"
            return

        git_out = stdout.decode(errors="replace").strip()
        yield f"data: [ROLLBACK] Checkout OK: {git_out or body.tag}\n\n"

        if body.mode == "yaml_only":
            yield f"data: [DONE] Rollback to tag {body.tag} complete (yaml_only)\n\n"
            return

        # Шаг 2 (yaml_and_apply): генерируем и запускаем плейбук
        yield f"data: [APPLY] Generating playbook for hosts: {', '.join(body.hosts)}\n\n"

        try:
            content = generate_apply_playbook(
                hosts=body.hosts,
                config_store_dir=CONFIG_STORE_DIR,
                mode="full",
                include_objects=False,
            )
            playbook_path = save_generated_playbook(content, CONFIG_STORE_DIR, "rollback-apply")
            logger.info("Сгенерирован rollback-плейбук: %s", playbook_path)
        except Exception as exc:
            logger.error("Ошибка генерации rollback-плейбука: %s", exc)
            yield f"data: [ERROR] Ошибка генерации плейбука: {exc}\n\n"
            return

        yield f"data: [APPLY] Playbook generated: {playbook_path}\n\n"

        gen = ar.stream_playbook(
            playbook_path,
            extra_vars={"backend_hosts": ",".join(body.hosts)},
        )
        async for line in gen:
            yield f"data: {line}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
