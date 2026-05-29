# Code Review: Playbook CRUD + Run + to-Profile API

**Date**: 2026-05-29
**Scope**: New file `backend/api/playbooks.py` + router registration in `backend/main.py`
**Files**: 2 | **Changes**: +186 / -1

## Summary

|              | Critical | High | Medium | Low |
| ------------ | -------- | ---- | ------ | --- |
| Issues       | 0        | 0    | 1      | 2   |
| Improvements | —        | 0    | 1      | 1   |

**Verdict**: PASS (with minor fixes recommended)

---

## Issues

### Medium

#### 1. Null-byte injection not blocked in `_safe_path`

- **File**: `backend/api/playbooks.py:37-42`
- **Problem**: `_safe_path` rejects `/`, `\`, and `..` but does not strip or reject null bytes (`\x00`). On Linux, `Path("evil\x00.yml")` passes the suffix check and resolves to `_generated/evil` (the OS truncates at the null byte), landing outside the expected filename.
- **Impact**: Attacker-controlled name like `legitimate\x00.yml` could potentially reference a different file than the UI shows. Low exploitability in this internal tool, but a correctness bug.
- **Fix**: Add `if "\x00" in name:` to the guard block, or normalize with `name = name.replace("\x00", "")` before the checks.

```python
def _safe_path(name: str) -> Path:
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise HTTPException(400, "Invalid playbook name")
```

### Low

#### 2. No maximum length check on `name` path parameter

- **File**: `backend/api/playbooks.py:37`
- **Problem**: A 300-character name passes `_safe_path` and gets handed to the filesystem. Most OS kernels reject filenames longer than 255 bytes, raising an `OSError` that propagates as an unhandled 500.
- **Impact**: Unexpected 500 instead of a clean 400 for invalid input.
- **Fix**: Add `if len(name) > 255: raise HTTPException(400, "Playbook name too long")`.

#### 3. `CONFIG_STORE_DIR` snapshotted at import time

- **File**: `backend/api/playbooks.py:30`
- **Problem**: `CONFIG_STORE_DIR` is read once at module import. If the env var is set after the process starts (e.g., via `.env` loaded by a startup hook after `import`), the module will use the default `/opt/ivamail-config-store`. This is the same pattern used in `config_store.py`, so it is consistent, but it is a latent misconfiguration trap.
- **Impact**: Silent misconfiguration if env is set late. No runtime crash.
- **Fix** (optional): Move the read into `_gen_dir()` — `Path(os.environ.get("CONFIG_STORE_DIR", "/opt/ivamail-config-store")) / "_generated"` — so it is always fresh. Alternatively, document the requirement to set `CONFIG_STORE_DIR` before process start.

---

## Improvements

### Medium

#### 1. `to-profile` regex is fragile against real playbook YAML structure

- **File**: `backend/api/playbooks.py:172-176`
- **Problem**: The conversion regex `--module\s+(\S+)(.*?)(?=\n\s*-\s|\Z)` assumes playbooks use shell `--module`/`--kv` arguments. Generated playbooks in this codebase (see `playbook_generator.py`) use native Ansible YAML task structure, not CLI flags. The regex will match nothing on real generated playbooks, always returning 422.
- **Impact**: The `to-profile` endpoint will be non-functional for all currently generated playbooks.
- **Fix**: This is either intentional (the endpoint targets a different playbook format) or a design mismatch. Clarify with the team. If targeting generated playbooks, parse the YAML task list for `module_args` / `args` keys instead.

### Low

#### 2. `PlaybookMeta.path` exposes server filesystem path to clients

- **File**: `backend/api/playbooks.py:47`
- **Problem**: `path: str` is the absolute server path (e.g., `/opt/ivamail-config-store/_generated/apply-20240115T103000.yml`). This leaks the server directory layout to the browser.
- **Impact**: Information disclosure. Low severity for an internal tool but unnecessary.
- **Fix**: Omit `path` from the response model, or return only the filename (which is already in `name`).

---

## Positive Patterns

1. **`_safe_path` path traversal guard** — Explicitly blocks `/`, `\`, and `..` before constructing the filesystem path. The right approach for a write-capable endpoint.
2. **SSE `[DONE]` sentinel in `finally`** — Guarantees the client always receives a terminal event even if `stream_playbook` raises, preventing hung client connections.
3. **`env or None` pattern for empty credentials** — Cleanly avoids passing an empty dict to `stream_playbook`, falling back to ambient `os.environ` only when no credentials are provided.

---

## Escalation

- **New API contract** at `/api/config/playbooks/*`: frontend teams should be notified of the 6 new endpoints.
- **`to-profile` design question** (see Improvement #1): requires a decision on whether this endpoint targets generated YAML playbooks or a different CLI-arg format. Should be clarified before the endpoint is exposed to users.

---

## Validation

- Import smoke test: **PASS** (6 routes loaded)
- Type check: not run (no `mypy`/`pyright` config detected in repo)
- Build: not applicable (no build step for this Python service)
