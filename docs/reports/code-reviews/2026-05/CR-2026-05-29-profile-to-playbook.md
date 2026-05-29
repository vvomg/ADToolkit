# Code Review: Profile→Playbook conversion endpoint

**Date**: 2026-05-29
**Scope**: `backend/api/profiles.py`, `backend/services/playbook_generator.py`
**Files**: 2 | **Changes**: +129 / -83

## Summary

|              | Critical | High | Medium | Low |
| ------------ | -------- | ---- | ------ | --- |
| Issues       | 0        | 1    | 0      | 0   |
| Improvements | —        | 1    | 1      | 0   |

**Verdict**: PASS (after fixes applied inline)

## Issues

### High

#### 1. Broad `except Exception` masked real HTTPException from `get_profile`

- **File**: `backend/api/profiles.py` (original endpoint)
- **Problem**: `ps.get_profile()` already raises `HTTPException(404)` directly. Wrapping it in a bare `except Exception: raise HTTPException(404, ...)` swallowed the existing error detail and would also incorrectly return 404 for unrelated errors (e.g., file I/O failure during profile read).
- **Impact**: Any runtime error inside `get_profile` — e.g., a corrupt YAML file — would surface as a misleading 404 instead of 500, hiding real failures.
- **Fix**: Removed the try/except entirely; `get_profile`'s own HTTPException propagates correctly through FastAPI. Applied in this review.

## Improvements

### High

#### 1. Inline imports inside endpoint handler

- **File**: `backend/api/profiles.py` (original endpoint)
- **Problem**: `import os`, `from pathlib import Path`, `from fastapi import HTTPException`, and `from ..services.playbook_generator import ...` were placed inside the function body, meaning Python re-evaluates the import machinery on every request.
- **Recommended**: Hoist to module level. Also removed the redundant `from fastapi import HTTPException` inside `_cmd()` that had survived from before the module-level import existed.
- **Applied**: Yes.

### Medium

#### 1. `mode` field on `ToPlaybookRequest` accepts arbitrary strings

- **File**: `backend/api/profiles.py:103`
- **Problem**: `mode: str = "full"` has no validation. `generate_apply_playbook` silently treats any unknown mode as "full" (no diff branch triggers), but passing `mode="diff"` without `diff_data` produces a full playbook anyway — potentially confusing.
- **Recommended**: Add a `mode` allowlist guard in the endpoint (`if body.mode not in ("full", "diff"): raise HTTPException(400, ...)`). Applied in this review.

## Positive Patterns

1. **Minimal-change design**: The `profile_modules` branch is an early-exit in `generate_apply_playbook` that reuses all existing helpers (`_config_to_kv_args`, `_task_module`, the header/play1/play2 structure). No new abstractions invented.
2. **`save_generated_playbook` reuse**: The endpoint correctly delegates file persistence to the existing helper rather than reimplementing it, including the timestamped filename and `_generated/` directory convention.
3. **Consistent error signaling**: After the fix, the endpoint follows the exact same pattern as all other `profiles.py` endpoints — call `ps.get_profile()` and let its HTTPException propagate unchanged.

## Escalation

- **New API endpoint** `POST /api/config/profiles/{slug}/to-playbook`: New contract, should be documented in the module docstring at the top of `profiles.py` (the existing endpoint table). Minor — does not block merge.

## Validation

- Syntax Check: PASS (`python -m py_compile` both files)
- Functional Test: PASS (profile_modules branch generates correct YAML, both hosts, kv-args verified)
- Edge Cases: PASS (empty `profile_modules={}` produces placeholder task; `profile_modules=None` falls through to disk path correctly)
- Smoke Test: PASS (`profile_modules` param present; `to-playbook` route registered)
