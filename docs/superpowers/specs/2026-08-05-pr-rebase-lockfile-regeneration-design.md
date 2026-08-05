# pr-rebase: Lockfile Regeneration After Conflict Resolution

## Problem

When `pr-rebase --fix` resolves conflicts in lockfiles (pnpm-lock.yaml, yarn.lock, etc.), it accepts the "theirs" version but never regenerates it. The stale lockfile causes the pre-push hook build to fail. The existing `_fix_push_failures` AI recovery tries to edit the lockfile content — but lockfiles need a command (`pnpm install`), not a text edit.

The `go.sum` special case works because it has a hardcoded `go mod tidy` step, but this approach doesn't scale to every ecosystem.

## Design

### Classification-Driven Resolution

Replace the scattered special cases (`go.sum` check in `_resolve_one`, generated file check in `_resolve_file_conflicts`, `_resolve_generated`) with a single classification function that determines the resolution strategy for each conflicted file:

```python
def _classify_conflict(filepath, full_path, cwd):
    # 1. Known lockfile → accept theirs, regenerate after
    regen = _find_regenerator(filepath)
    if regen:
        return ("regenerate", regen)

    # 2. Detected generated (gitattributes/header) → accept theirs, try AI regen
    generated, method = _is_generated_file(filepath, full_path, cwd)
    if generated:
        return ("accept_theirs", method)

    # 3. Delete conflict → accept deletion
    delete_type = _detect_delete_conflict(filepath, cwd)
    if delete_type:
        return ("delete", delete_type)

    # 4. Binary → can't resolve
    if _is_binary(full_path):
        return ("binary_error", None)

    # 5. Text → AI merge
    return ("ai_merge", None)
```

The caller dispatches on the strategy tag. This replaces `_resolve_one` and `_resolve_generated`.

### Regenerator Registry

An inline dict maps lockfile basenames to regeneration commands:

```python
_LOCKFILE_REGENERATORS = {
    "go.sum":            {"cmd": ["go", "mod", "tidy"], "stage_dir": True},
    "go.mod":            {"cmd": ["go", "mod", "tidy"], "stage_dir": True},
    "pnpm-lock.yaml":    {"cmd": ["pnpm", "install"]},
    "package-lock.json": {"cmd": ["npm", "install"]},
    "yarn.lock":         {"cmd": ["yarn", "install"]},
    "bun.lock":          {"cmd": ["bun", "install"]},
    "bun.lockb":         {"cmd": ["bun", "install"]},
    "Cargo.lock":        {"cmd": ["cargo", "generate-lockfile"]},
    "uv.lock":           {"cmd": ["uv", "lock"]},
    "poetry.lock":       {"cmd": ["poetry", "lock", "--no-update"]},
    "composer.lock":     {"cmd": ["composer", "install"]},
    "Gemfile.lock":      {"cmd": ["bundle", "install"]},
}
```

Lookup is `os.path.basename(filepath)`. `stage_dir: True` means `git add -u` in the directory after regeneration (for commands like `go mod tidy` that touch multiple files). Default is `git add <lockfile>` only.

### Tool Version Resolution

Before running a regeneration command, detect whether the project uses a tool version manager:

1. Check if `mise` is on PATH
2. Check for `mise.toml` or `.tool-versions` in the target directory or any ancestor up to the repo root
3. If found, wrap the command with `mise exec --`
4. If the bare command fails with "not found", retry once with `mise exec --` even if no config file was detected

### Deduplication

Regeneration commands accumulate during the per-file loop as `pending_regens: dict[(dir, cmd_tuple), list[filepath]]`. After all conflicts in a commit are resolved, each unique (directory, command) pair runs once. This handles:
- `go.sum` and `go.mod` in the same directory → one `go mod tidy`
- Multiple lockfiles in different subdirectories → each runs independently

### AI Fallback

When a file is detected as generated (via gitattributes/header) but isn't in the registry, prompt the AI to suggest a regeneration command:

```
A merge conflict was resolved by accepting theirs for a generated file.
File: <filepath>
Project root: <cwd>
Nearby files: <ls of the file's directory>

What single shell command regenerates this file from its source?
Reply with ONLY the command (e.g. "pnpm install"), or "NONE" if no regeneration is needed.
```

Uses `ai_backend.prompt()` (stateless, no agent). Guarded by `ai_backend.is_available()` — if unavailable, skip silently. If the response is a recognized command, run it through the same regeneration path with mise wrapping. If "NONE" or unparseable, accept theirs with no warning (debug-level trail log only).

This fires rarely — only for generated files not in the built-in registry.

### Error Handling

If regeneration fails, log a warning and record the failure in the trail, but do not abort the rebase. `_run_regeneration` returns a boolean (success/failure) but the caller always continues. The pre-push hook catches stale lockfiles downstream, and `_fix_push_failures` gets another shot. This softens the current Go behavior where `go mod tidy` failure aborts the entire rebase.

### Execution Flow

```
_resolve_file_conflicts(conflicts, cwd, sha, subject)
    pending_regens: dict[(dir, cmd_tuple)] → list[filepath]

    for filepath in conflicts:
        strategy, context = _classify_conflict(filepath, full_path, cwd)

        "regenerate"    → _accept_theirs_and_stage, add to pending_regens
        "accept_theirs" → _accept_theirs_and_stage, AI regen prompt if available
        "delete"        → _resolve_delete_conflict
        "binary_error"  → return None
        "ai_merge"      → _resolve_single_file

    for (dir, cmd), files in pending_regens:
        _run_regeneration(dir, cmd, files)

    return resolved
```

## Changes

### Deleted
- `_resolve_one` — dispatch logic moves into `_classify_conflict`
- `_resolve_generated` — absorbed into the "accept_theirs" dispatch branch
- The `go_dirs` set and post-loop `go mod tidy` block in `_resolve_file_conflicts`
- The `go.sum`/`go.mod` special cases (lines 732-754)

### Modified
- `_resolve_file_conflicts` — new loop structure with classify → dispatch → deferred regen
- `_accept_theirs_and_stage` — remove misleading "will regenerate" message, just "Accepted theirs"

### Added
- `_classify_conflict(filepath, full_path, cwd)` — single decision function
- `_find_regenerator(filepath)` — registry lookup by basename
- `_run_regeneration(dir, cmd, files)` — execute regen command with mise wrapping, stage result
- `_detect_mise(dir, repo_root)` — check for mise availability and config
- `_LOCKFILE_REGENERATORS` dict
- AI fallback prompt for unknown generated files

### Unchanged
- `_accept_theirs_and_stage` (signature)
- `_resolve_single_file` / `_resolve_full_file` / `_resolve_chunked` (AI merge path)
- `_resolve_delete_conflict`
- `_is_generated_file`
- `_fix_push_failures` (downstream safety net)
- All rebase lifecycle functions (`_drive_to_completion`, `_fresh`, etc.)
