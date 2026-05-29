# Code Review: Playbooks Tab + Profile→Playbook Button

**Date**: 2026-05-29
**Scope**: ConfigManagement.tsx — Playbooks tab body, profile card → Playbook action
**Files**: 1 | **Changes**: +388 / -24

## Summary

|              | Critical | High | Medium | Low |
| ------------ | -------- | ---- | ------ | --- |
| Issues       | 0        | 0    | 2      | 1   |
| Improvements | —        | 0    | 1      | 1   |

**Verdict**: PASS (2 issues fixed inline before commit)

## Issues

### Medium (Fixed in this review)

#### 1. `savePlaybookEdit` — silent failure, editor closes on error

- **File**: `frontend/src/pages/ConfigManagement.tsx` (PlaybooksTab)
- **Problem**: Original code called `setPbSaving(false); setEditPbName(null)` unconditionally after `await fetch(...)`. If the PUT request failed (network error, 500), the editor would close silently losing the user's edit context.
- **Impact**: User thinks save succeeded; edits are lost.
- **Fix Applied**: Wrapped in try/finally, `setEditPbName(null)` only called when `r.ok`.

#### 2. `deletePlaybook` — optimistic UI update before confirming server success

- **File**: `frontend/src/pages/ConfigManagement.tsx` (PlaybooksTab)
- **Problem**: `setPlaybooks(prev => prev.filter(...))` executed regardless of whether DELETE returned 2xx.
- **Impact**: Item disappears from UI but still exists on server; inconsistent state until next `loadPlaybooks()`.
- **Fix Applied**: Checks `r.ok` before updating local state.

### Low

#### 3. `loadPlaybookContent` — unhandled fetch errors

- **File**: `frontend/src/pages/ConfigManagement.tsx:~3323`
- **Problem**: No try/catch. If the GET fails, `editPbName` is set (showing the editor) but `editPbContent` stays empty — confusing UX showing a blank editor panel.
- **Impact**: Low — network failures are rare, and a blank textarea is recoverable (user can cancel).
- **Fix**: Wrap in try/catch; call `setEditPbName(null)` on error.

## Improvements

### Medium

#### 1. `pbCmdUser`/`pbCmdPass` stale after credential change

- **File**: `frontend/src/pages/ConfigManagement.tsx` (PlaybooksTab)
- **Current**: Initialized from `creds.user` / `creds.pass` at mount time only.
- **Problem**: If the user updates credentials via `CredsBar` after the tab is first rendered, the playbook run panel shows stale pre-fills. Typing in the run-panel inputs overwrites, so it's not blocking — just confusing.
- **Recommended**: Use `useEffect` syncing `pbCmdUser`/`pbCmdPass` with `creds.user`/`creds.pass` when they change, only if the user hasn't manually overridden them yet.

### Low

#### 2. Profile→Playbook button opacity-0 on non-hover is invisible to keyboard users

- **File**: `frontend/src/pages/ConfigManagement.tsx:~2310`
- **Current**: `opacity-0 group-hover:opacity-100` hides the button completely when the parent is not hovered.
- **Problem**: Keyboard focus via Tab will reach the button but it will be invisible, making it inaccessible without mouse.
- **Recommended**: Add `focus-within:opacity-100` to the wrapper or `focus:opacity-100` on the button itself.

## Positive Patterns

1. **Auto-refresh after dump** — hooking `loadPlaybooks()` into the dump `onDone` callback is a clean, correct pattern that avoids stale list state.
2. **`[DONE]` sentinel + 200-line ring buffer** — the SSE reader in `runPlaybook` correctly handles the completion sentinel and caps memory growth with `.slice(-200)`.
3. **Consistent Catppuccin Mocha styling** — all new UI elements use the established `bg-surface0/surface1/mantle/crust`, `text-overlay0/subtext/text`, and accent color patterns with no one-offs.

## Validation

- Type Check: PASS
- Build: PASS (`✓ built in 1.29s`)
