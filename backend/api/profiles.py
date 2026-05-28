"""
api/profiles.py — Configuration Profiles REST API.

Endpoints
---------
  GET    /api/config/profiles                          → {profiles: ProfileMeta[]}
  POST   /api/config/profiles                          → {profile: ProfileFull}   body: {name, notes?}
  GET    /api/config/profiles/{slug}                   → {profile: ProfileFull}
  PUT    /api/config/profiles/{slug}                   → {profile: ProfileFull}   body: {name?, notes?}
  DELETE /api/config/profiles/{slug}                   → {deleted: true}
  PUT    /api/config/profiles/{slug}/modules/{module}  → {profile: ProfileFull}   body: {config: dict}
  DELETE /api/config/profiles/{slug}/modules/{module}  → {profile: ProfileFull}
  POST   /api/config/profiles/{slug}/pull              → {profile: ProfileFull}   body: {ip, modules, cmd_user, cmd_password, port?}
  POST   /api/config/profiles/{slug}/duplicate         → {profile: ProfileFull}   body: {name: str}
  POST   /api/config/profiles/{slug}/apply/stream      → StreamingResponse (SSE)  body: {hosts, modules?, cmd_user, cmd_password}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..infrastructure.cmd_client import CMDClient, CMDError
from ..services import profile_store as ps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/profiles", tags=["profiles"])


# ---------------------------------------------------------------------------
# CMD session helper — same pattern as config_store.py
# ---------------------------------------------------------------------------

async def _cmd(ip: str, port: int, user: str, password: str) -> CMDClient:
    from fastapi import HTTPException
    client = CMDClient(host=ip, port=port)
    try:
        await client.connect()
        await client.authenticate(user, password)
    except Exception as exc:
        await client.close()
        raise HTTPException(status_code=401, detail=f"CMD auth failed on {ip}: {exc}") from exc
    return client


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateProfileRequest(BaseModel):
    name: str
    notes: str = ""


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


class UpsertModuleRequest(BaseModel):
    config: dict[str, Any]


class PullRequest(BaseModel):
    ip: str
    modules: list[str]
    cmd_user: str
    cmd_password: str
    port: int = 106


class DuplicateRequest(BaseModel):
    name: str


class ApplyRequest(BaseModel):
    hosts: list[str]
    modules: Optional[list[str]] = None   # None → all modules in profile; [] means skip all
    cmd_user: str
    cmd_password: str
    port: int = 106
    mode: str = "cmd"   # "cmd" | "ansible"


class PullAllStreamRequest(BaseModel):
    """Запрос для SSE-стриминга pull со всех нод."""
    ips: list[str]
    cmd_user: str
    cmd_password: str
    port: int = 106


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_module_config(resp_text: str) -> dict[str, Any]:
    """Parse CMDClient module_read_config response text → dict."""
    try:
        parsed = json.loads(resp_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_profiles() -> dict[str, Any]:
    """Return all profiles (metadata only, no module content)."""
    profiles = ps.list_profiles()
    return {"profiles": [p.model_dump() for p in profiles]}


@router.post("")
async def create_profile(req: CreateProfileRequest) -> dict[str, Any]:
    """Create a new empty profile."""
    profile = ps.create_profile(name=req.name, notes=req.notes)
    await ps._git_commit(f"profiles: create '{profile.slug}'")
    return {"profile": profile.model_dump()}


@router.get("/{slug}")
async def get_profile(slug: str) -> dict[str, Any]:
    """Return full profile including all module configs."""
    profile = ps.get_profile(slug)
    return {"profile": profile.model_dump()}


@router.put("/{slug}")
async def update_profile(slug: str, req: UpdateProfileRequest) -> dict[str, Any]:
    """Update profile name and/or notes. Renames file if name changes."""
    profile = ps.update_profile(slug=slug, name=req.name, notes=req.notes)
    await ps._git_commit(f"profiles: update '{profile.slug}'")
    return {"profile": profile.model_dump()}


@router.delete("/{slug}")
async def delete_profile(slug: str) -> dict[str, Any]:
    """Delete a profile."""
    ps.delete_profile(slug)
    await ps._git_commit(f"profiles: delete '{slug}'")
    return {"deleted": True}


@router.put("/{slug}/modules/{module}")
async def upsert_module(
    slug: str,
    module: str,
    req: UpsertModuleRequest,
) -> dict[str, Any]:
    """Add or replace a module config inside a profile."""
    profile = ps.upsert_module(slug=slug, module=module, config=req.config)
    await ps._git_commit(f"profiles: upsert module '{module}' in '{slug}'")
    return {"profile": profile.model_dump()}


@router.delete("/{slug}/modules/{module}")
async def remove_module(slug: str, module: str) -> dict[str, Any]:
    """Remove a module from a profile."""
    profile = ps.remove_module(slug=slug, module=module)
    await ps._git_commit(f"profiles: remove module '{module}' from '{slug}'")
    return {"profile": profile.model_dump()}


@router.post("/{slug}/pull")
async def pull_modules(slug: str, req: PullRequest) -> dict[str, Any]:
    """
    Read each listed module config from a live node via CMD and upsert into profile.
    """
    # Verify profile exists first (raises 404 if not)
    ps.get_profile(slug)

    client = await _cmd(req.ip, req.port, req.cmd_user, req.cmd_password)
    errors: list[str] = []

    try:
        for module in req.modules:
            try:
                resp = await client.module_read_config(module)
                if not resp.ok:
                    logger.warning(
                        "Pull: ModuleReadConfig '%s' from %s returned %d: %s",
                        module, req.ip, resp.code, resp.text[:100],
                    )
                    errors.append(f"{module}: server error {resp.code}")
                    continue
                config = _parse_module_config(resp.text)
                ps.upsert_module(slug=slug, module=module, config=config)
                logger.info("Pulled module '%s' from %s → profile '%s'", module, req.ip, slug)
            except CMDError as exc:
                logger.error("Pull CMD error for module '%s': %s", module, exc)
                errors.append(f"{module}: {exc}")
    finally:
        await client.close()

    profile = ps.get_profile(slug)
    await ps._git_commit(
        f"profiles: pull {len(req.modules)} module(s) from {req.ip} into '{slug}'"
    )

    result: dict[str, Any] = {"profile": profile.model_dump()}
    if errors:
        result["errors"] = errors
    return result


@router.post("/{slug}/duplicate")
async def duplicate_profile(slug: str, req: DuplicateRequest) -> dict[str, Any]:
    """Duplicate a profile under a new name."""
    profile = ps.duplicate_profile(slug=slug, new_name=req.name)
    await ps._git_commit(f"profiles: duplicate '{slug}' → '{profile.slug}'")
    return {"profile": profile.model_dump()}


@router.post("/{slug}/pull-all/stream")
async def pull_all_stream(slug: str, req: PullAllStreamRequest) -> StreamingResponse:
    """
    Параллельно опрашивает все IP из req.ips:
      1. Для каждого IP делает modules_list()
      2. Параллельно читает все модули через module_read_config()
      3. Стримит JSON-события для каждого модуля:
         {"type":"progress","ip":...,"module":...,"status":"ok","config":{...}}
         {"type":"error","ip":...,"module":...,"error":"..."}
         {"type":"connect_error","ip":...,"error":"..."}
      4. В конце: {"type":"done","total_ok":N,"total_err":M}

    Коллизии (одинаковый модуль с разными значениями) детектируются на клиенте.
    """
    # Verify profile exists
    ps.get_profile(slug)

    async def _fetch_ip(ip: str, counters: dict) -> list[str]:
        """Fetch all module configs from one IP. Returns list of JSON-encoded event strings."""
        events: list[str] = []

        # Connect
        try:
            client = await _cmd(ip, req.port, req.cmd_user, req.cmd_password)
        except Exception as exc:
            events.append(json.dumps({"type": "connect_error", "ip": ip, "error": str(exc)}))
            return events

        # Get module list
        modules: list[str] = []
        try:
            resp = await client.modules_list()
            if resp.ok:
                try:
                    raw = json.loads(resp.text)
                    if isinstance(raw, list):
                        modules = raw
                    elif isinstance(raw, dict):
                        modules = list(raw.values())
                    else:
                        modules = []
                except Exception:
                    modules = [line.strip() for line in resp.text.splitlines() if line.strip()]
            else:
                events.append(json.dumps({
                    "type": "error", "ip": ip, "module": "__list__",
                    "error": f"modules_list failed: {resp.text[:80]}",
                }))
        except Exception as exc:
            events.append(json.dumps({"type": "error", "ip": ip, "module": "__list__", "error": str(exc)}))

        if not modules:
            try:
                await client.close()
            except Exception:
                pass
            return events

        # Read module configs SEQUENTIALLY — CMD protocol uses a single TCP connection;
        # concurrent reads on the same StreamReader raise "readuntil() called while
        # another coroutine is already waiting for incoming data".
        # Parallelism happens at the IP level (separate connections), not within one IP.
        for module in modules:
            try:
                r = await client.module_read_config(module)
                if r.ok:
                    try:
                        cfg = json.loads(r.text)
                        if not isinstance(cfg, dict):
                            cfg = {}
                    except Exception:
                        cfg = {}
                    counters["ok"] += 1
                    events.append(json.dumps({"type": "progress", "ip": ip, "module": module,
                                              "status": "ok", "config": cfg}))
                else:
                    counters["err"] += 1
                    events.append(json.dumps({"type": "error", "ip": ip, "module": module,
                                              "error": f"server {r.code}: {r.text[:80]}"}))
            except CMDError as exc:
                counters["err"] += 1
                events.append(json.dumps({"type": "error", "ip": ip, "module": module,
                                          "error": str(exc)}))
            except Exception as exc:
                counters["err"] += 1
                events.append(json.dumps({"type": "error", "ip": ip, "module": module,
                                          "error": f"unexpected: {exc}"}))

        try:
            await client.close()
        except Exception:
            pass

        return events

    async def _generate():
        counters: dict = {"ok": 0, "err": 0}

        # Fetch all IPs in parallel; collect results then stream them
        all_ip_events: list[list[str]] = await asyncio.gather(
            *[_fetch_ip(ip, counters) for ip in req.ips]
        )

        for ip_events in all_ip_events:
            for event in ip_events:
                yield f"data: {event}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'total_ok': counters['ok'], 'total_err': counters['err']})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{slug}/apply/stream")
async def apply_stream(slug: str, req: ApplyRequest) -> StreamingResponse:
    """
    Apply profile module configs to one or more live nodes.

    mode="cmd"     → прямое применение через CMD протокол
    mode="ansible" → запуск Ansible-плейбука (генерация + ansible-runner)

    SSE stream events (строки data: ...):
      [10.3.6.206] Cluster → ok
      [10.3.6.206] SMTP → FAILED: ...
      [10.3.6.206] version: 3.2.1         (версия ПО до применения)
      [EXIT 0] Applied N modules to M hosts
      [EXIT 1] N errors, M ok — K hosts
    """
    from ..api.config_history import create_history_entry, CreateHistoryRequest
    from ..services import ansible_runner as ar

    profile = ps.get_profile(slug)

    # Determine which modules to apply
    if req.modules is not None:
        module_names = [m for m in req.modules if m in profile.modules]
        skipped = [m for m in req.modules if m not in profile.modules]
        if skipped:
            logger.warning("apply/stream: modules not in profile '%s': %s", slug, skipped)
    else:
        module_names = list(profile.modules.keys())

    async def _generate():
        total_ok = 0
        total_err = 0
        all_errors: list[str] = []
        node_versions: dict[str, str] = {}

        # ── Ansible mode ─────────────────────────────────────────────────────
        if req.mode == "ansible":
            yield "data: [ANSIBLE] Generating playbook from profile...\n\n"
            try:
                from ..services.playbook_generator import generate_apply_playbook, save_generated_playbook
                import os
                config_store_dir = os.environ.get("CONFIG_STORE_DIR", "/opt/ivamail-config-store")
                content = generate_apply_playbook(
                    hosts=req.hosts,
                    config_store_dir=config_store_dir,
                    mode="full",
                    include_objects=False,
                )
                playbook_path = save_generated_playbook(content, config_store_dir, f"profile-{slug}")
                yield f"data: [ANSIBLE] Playbook: {playbook_path}\n\n"

                gen = ar.stream_playbook(
                    playbook_path,
                    extra_vars={"backend_hosts": ",".join(req.hosts)},
                    env={"IVAMAIL_CMD_USER": req.cmd_user, "IVAMAIL_CMD_PASSWORD": req.cmd_password},
                )
                async for line in gen:
                    yield f"data: {line}\n\n"
                    if "[EXIT 0]" in line or "PLAY RECAP" in line:
                        total_ok = len(module_names) * len(req.hosts)
                    elif "FAILED" in line or "ERROR" in line:
                        total_err += 1
                        all_errors.append(line)

                status = "ok" if total_err == 0 else ("partial" if total_ok > 0 else "failed")
                try:
                    create_history_entry(CreateHistoryRequest(
                        profile_slug=slug,
                        profile_name=profile.name,
                        apply_mode="ansible",
                        playbook_path=str(playbook_path),
                        target_hosts=req.hosts,
                        modules_applied=module_names,
                        node_versions=node_versions,
                        status=status,
                        errors=all_errors or None,
                    ))
                except Exception as he:
                    logger.warning("Failed to write history: %s", he)
                return
            except Exception as exc:
                yield f"data: [ERROR] Ansible mode failed: {exc}\n\n"
                return

        # ── CMD mode ──────────────────────────────────────────────────────────
        for host in req.hosts:
            client: Optional[CMDClient] = None
            try:
                client = await _cmd(host, req.port, req.cmd_user, req.cmd_password)
            except Exception as exc:
                msg = f"[{host}] CONNECT FAILED: {exc}"
                yield f"data: {msg}\n\n"
                total_err += len(module_names)
                all_errors.append(msg)
                continue

            # Fetch SystemInfo for version
            try:
                si_resp = await client.system_info()
                if si_resp.ok and si_resp.text:
                    try:
                        si = json.loads(si_resp.text)
                        version = si.get("Server version", si.get("Version", "unknown"))
                    except Exception:
                        version = "unknown"
                    node_versions[host] = str(version)
                    yield f"data: [{host}] version: {version}\n\n"
            except Exception:
                pass  # Non-critical

            for module in module_names:
                module_config = profile.modules.get(module, {})
                try:
                    key_val_pairs: list[Any] = []
                    for k, v in module_config.items():
                        key_val_pairs.append(k)
                        key_val_pairs.append(v)

                    resp = await client.module_update_config(
                        module=module,
                        keys_to_delete=[],
                        key_val_pairs=key_val_pairs,
                    )
                    if resp.ok:
                        yield f"data: [{host}] {module} → ok\n\n"
                        total_ok += 1
                    else:
                        msg_txt = resp.text[:120].replace("\n", " ")
                        err = f"[{host}] {module} → FAILED: {resp.code} {msg_txt}"
                        yield f"data: {err}\n\n"
                        all_errors.append(err)
                        total_err += 1
                except CMDError as exc:
                    err = f"[{host}] {module} → FAILED: {exc}"
                    yield f"data: {err}\n\n"
                    all_errors.append(err)
                    total_err += 1
                except Exception as exc:
                    err = f"[{host}] {module} → FAILED: unexpected: {exc}"
                    yield f"data: {err}\n\n"
                    all_errors.append(err)
                    total_err += 1

            try:
                await client.close()
            except Exception:
                pass

        if total_err == 0:
            yield f"data: [EXIT 0] Applied {total_ok} module(s) to {len(req.hosts)} host(s)\n\n"
            status = "ok"
        else:
            yield f"data: [EXIT 1] {total_err} error(s), {total_ok} ok — {len(req.hosts)} host(s)\n\n"
            status = "partial" if total_ok > 0 else "failed"

        # Write to history
        try:
            create_history_entry(CreateHistoryRequest(
                profile_slug=slug,
                profile_name=profile.name,
                apply_mode="cmd",
                playbook_path=None,
                target_hosts=req.hosts,
                modules_applied=module_names,
                node_versions=node_versions,
                status=status,
                errors=all_errors or None,
            ))
        except Exception as he:
            logger.warning("Failed to write apply history: %s", he)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
