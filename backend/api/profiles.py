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
    modules: Optional[list[str]] = None   # None → all modules in profile
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


@router.post("/{slug}/apply/stream")
async def apply_stream(slug: str, req: ApplyRequest) -> StreamingResponse:
    """
    Apply profile module configs to one or more live nodes via CMD.

    SSE stream — each line is:
      data: [{host}] {module} → ok\n\n
      data: [{host}] {module} → FAILED: {err}\n\n
      data: [EXIT 0] Applied N modules to M hosts\n\n   (or EXIT 1 on errors)
    """
    profile = ps.get_profile(slug)

    # Determine which modules to apply
    if req.modules:
        module_names = [m for m in req.modules if m in profile.modules]
        skipped = [m for m in req.modules if m not in profile.modules]
        if skipped:
            logger.warning(
                "apply/stream: modules not found in profile '%s': %s", slug, skipped
            )
    else:
        module_names = list(profile.modules.keys())

    async def _generate():
        total_ok = 0
        total_err = 0

        for host in req.hosts:
            client: Optional[CMDClient] = None
            try:
                client = await _cmd(host, req.port, req.cmd_user, req.cmd_password)
            except Exception as exc:
                yield f"data: [{host}] CONNECT FAILED: {exc}\n\n"
                total_err += len(module_names)
                continue

            for module in module_names:
                module_config = profile.modules.get(module, {})
                try:
                    # Build key_val_pairs flat list from config dict:
                    # ["Key1", val1, "Key2", val2, ...]
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
                        msg = resp.text[:120].replace("\n", " ")
                        yield f"data: [{host}] {module} → FAILED: {resp.code} {msg}\n\n"
                        total_err += 1
                except CMDError as exc:
                    yield f"data: [{host}] {module} → FAILED: {exc}\n\n"
                    total_err += 1
                except Exception as exc:
                    yield f"data: [{host}] {module} → FAILED: unexpected error: {exc}\n\n"
                    total_err += 1

            try:
                await client.close()
            except Exception:
                pass

        if total_err == 0:
            yield (
                f"data: [EXIT 0] Applied {total_ok} module(s) to "
                f"{len(req.hosts)} host(s)\n\n"
            )
        else:
            yield (
                f"data: [EXIT 1] {total_err} error(s), "
                f"{total_ok} ok — {len(req.hosts)} host(s)\n\n"
            )

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
