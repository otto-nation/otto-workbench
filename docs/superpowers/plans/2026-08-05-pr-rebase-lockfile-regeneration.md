# pr-rebase Lockfile Regeneration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered lockfile/generated-file special cases in pr-rebase with a unified classification → dispatch → regeneration flow that handles any ecosystem's lockfiles.

**Architecture:** A `_classify_conflict()` function determines resolution strategy for each conflicted file (regenerate, accept-theirs, delete, binary-error, ai-merge). A `_LOCKFILE_REGENERATORS` registry maps lockfile basenames to regeneration commands. Regeneration commands run through mise when available. An AI fallback handles generated files not in the registry.

**Tech Stack:** Python 3 (stdlib only — subprocess, shutil, os, pathlib), ai_backend module for AI fallback

## Global Constraints

- Source file: `ai/claude/bin/pr-rebase` (extensionless Python script)
- Test file: `tests/pr_rebase_test.py`
- Tests import via `importlib.machinery.SourceFileLoader` — the module is referenced as `pr_rebase_cli`
- Tests use `unittest.mock` and `subprocess.CompletedProcess` — follow existing patterns
- Run tests: `pytest tests/pr_rebase_test.py -v`
- No new dependencies — stdlib and existing `ai_backend` / `log` / `trail` modules only
- Regeneration failure must not abort the rebase — log warning, continue
- The `_trail` module-level variable is used for instrumentation (may be None)

---

### Task 1: Add the regenerator registry and lookup function

**Files:**
- Modify: `ai/claude/bin/pr-rebase:100-104` (after `_PICK_COMMANDS`, before git helpers section)

**Interfaces:**
- Produces: `_LOCKFILE_REGENERATORS: dict[str, dict]`, `_find_regenerator(filepath: str) -> dict | None`

- [ ] **Step 1: Write the failing tests**

```python
# ── _find_regenerator ──────────────────────────────────────────────────────


def test_find_regenerator_known_lockfile():
    result = pr_rebase_cli._find_regenerator("pnpm-lock.yaml")
    assert result is not None
    assert result["cmd"] == ["pnpm", "install"]


def test_find_regenerator_go_sum():
    result = pr_rebase_cli._find_regenerator("go.sum")
    assert result is not None
    assert result["cmd"] == ["go", "mod", "tidy"]
    assert result.get("stage_dir") is True


def test_find_regenerator_nested_path():
    """Lookup uses basename, not full path."""
    result = pr_rebase_cli._find_regenerator("packages/web/pnpm-lock.yaml")
    assert result is not None
    assert result["cmd"] == ["pnpm", "install"]


def test_find_regenerator_unknown_file():
    result = pr_rebase_cli._find_regenerator("main.go")
    assert result is None


def test_find_regenerator_all_entries_have_cmd():
    """Every registry entry must have a 'cmd' key with a non-empty list."""
    for name, entry in pr_rebase_cli._LOCKFILE_REGENERATORS.items():
        assert "cmd" in entry, f"{name} missing 'cmd'"
        assert isinstance(entry["cmd"], list) and len(entry["cmd"]) > 0, f"{name} has invalid 'cmd'"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pr_rebase_test.py::test_find_regenerator_known_lockfile -v`
Expected: FAIL with `AttributeError: module 'pr_rebase_cli' has no attribute '_find_regenerator'`

- [ ] **Step 3: Write the registry and lookup function**

Add after the `_PICK_COMMANDS` set (around line 104), before the `# ── Git helpers` section:

```python
# ── Lockfile regeneration registry ─────────────────────────────────────────

_LOCKFILE_REGENERATORS: dict[str, dict] = {
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


def _find_regenerator(filepath: str) -> dict | None:
    """Look up a regeneration command by lockfile basename."""
    return _LOCKFILE_REGENERATORS.get(os.path.basename(filepath))
```

Add `import os` at the top if not already present — verify with `grep`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pr_rebase_test.py -k "find_regenerator" -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```
git add ai/claude/bin/pr-rebase tests/pr_rebase_test.py
git commit -m "feat(pr-rebase): add lockfile regenerator registry"
```

---

### Task 2: Add mise detection and regeneration runner

**Files:**
- Modify: `ai/claude/bin/pr-rebase` (after `_find_regenerator`, before `_accept_theirs_and_stage`)

**Interfaces:**
- Consumes: `_LOCKFILE_REGENERATORS` dict entries with `cmd` and optional `stage_dir` keys
- Produces: `_detect_mise(target_dir: str, repo_root: str) -> bool`, `_run_regeneration(regen_dir: str, cmd: list[str], files: list[str], *, stage_dir: bool = False, cwd: str) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# ── _detect_mise ───────────────────────────────────────────────────────────


def test_detect_mise_found(tmp_path):
    (tmp_path / "mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is True


def test_detect_mise_tool_versions(tmp_path):
    (tmp_path / ".tool-versions").write_text("nodejs 20\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is True


def test_detect_mise_in_ancestor(tmp_path):
    subdir = tmp_path / "packages" / "web"
    subdir.mkdir(parents=True)
    (tmp_path / "mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(subdir), str(tmp_path)) is True


def test_detect_mise_not_installed(tmp_path):
    (tmp_path / "mise.toml").write_text("[tools]\n")
    with mock.patch("shutil.which", return_value=None):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is False


def test_detect_mise_no_config(tmp_path):
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(tmp_path), str(tmp_path)) is False


def test_detect_mise_stops_at_repo_root(tmp_path):
    """Does not search above repo_root."""
    repo = tmp_path / "repo"
    subdir = repo / "packages" / "web"
    subdir.mkdir(parents=True)
    (tmp_path / "mise.toml").write_text("[tools]\n")  # above repo root
    with mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        assert pr_rebase_cli._detect_mise(str(subdir), str(repo)) is False


# ── _run_regeneration ──────────────────────────────────────────────────────


def test_run_regeneration_bare_command(tmp_path):
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text("old content")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False):
        result = pr_rebase_cli._run_regeneration(
            str(tmp_path), ["pnpm", "install"], ["pnpm-lock.yaml"],
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["pnpm", "install"] in cmds
    assert ["git", "add", "pnpm-lock.yaml"] in cmds


def test_run_regeneration_with_mise(tmp_path):
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text("old content")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=True):
        result = pr_rebase_cli._run_regeneration(
            str(tmp_path), ["pnpm", "install"], ["pnpm-lock.yaml"],
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["mise", "exec", "--", "pnpm", "install"] in cmds


def test_run_regeneration_bare_fails_retries_mise(tmp_path):
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text("old content")
    calls = []
    run_count = [0]

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        if cmd == ["pnpm", "install"]:
            run_count[0] += 1
            return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr="command not found")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False), \
         mock.patch("shutil.which", return_value="/usr/local/bin/mise"):
        result = pr_rebase_cli._run_regeneration(
            str(tmp_path), ["pnpm", "install"], ["pnpm-lock.yaml"],
            cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["mise", "exec", "--", "pnpm", "install"] in cmds


def test_run_regeneration_stage_dir(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("cwd")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False):
        result = pr_rebase_cli._run_regeneration(
            str(tmp_path), ["go", "mod", "tidy"], ["go.sum"],
            stage_dir=True, cwd=str(tmp_path),
        )

    assert result is True
    cmds = [c[0] for c in calls]
    assert ["git", "add", "-u"] in cmds


def test_run_regeneration_failure_returns_false(tmp_path):
    def fake_run(cmd, **kwargs):
        if cmd[0] in ("pnpm", "mise"):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(pr_rebase_cli, "_detect_mise", return_value=False), \
         mock.patch("shutil.which", return_value=None):
        result = pr_rebase_cli._run_regeneration(
            str(tmp_path), ["pnpm", "install"], ["pnpm-lock.yaml"],
            cwd=str(tmp_path),
        )

    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pr_rebase_test.py::test_detect_mise_found -v`
Expected: FAIL with `AttributeError: module 'pr_rebase_cli' has no attribute '_detect_mise'`

- [ ] **Step 3: Write the implementation**

Add after `_find_regenerator`, before `_accept_theirs_and_stage`:

```python
def _detect_mise(target_dir: str, repo_root: str) -> bool:
    """Check if mise is available and configured for this directory."""
    if not shutil.which("mise"):
        return False
    d = Path(target_dir).resolve()
    root = Path(repo_root).resolve()
    while True:
        if (d / "mise.toml").is_file() or (d / ".tool-versions").is_file():
            return True
        if d == root or d.parent == d:
            return False
        d = d.parent


def _run_regeneration(
    regen_dir: str, cmd: list[str], files: list[str],
    *, stage_dir: bool = False, cwd: str,
) -> bool:
    """Run a regeneration command and stage the result. Returns success."""
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=cwd,
    ).stdout.strip() or cwd

    use_mise = _detect_mise(regen_dir, repo_root)
    run_cmd = ["mise", "exec", "--"] + cmd if use_mise else cmd

    log.info(f"Regenerating: {' '.join(run_cmd)} (in {Path(regen_dir).name}/)")
    r = subprocess.run(run_cmd, capture_output=True, text=True, cwd=regen_dir)

    if not use_mise and r.returncode == 127:
        if shutil.which("mise"):
            log.info("Retrying with mise...")
            run_cmd = ["mise", "exec", "--"] + cmd
            r = subprocess.run(run_cmd, capture_output=True, text=True, cwd=regen_dir)

    if r.returncode != 0:
        _terr("regeneration", f"{' '.join(cmd)} failed in {regen_dir}",
              data={"cmd": cmd, "exit_code": r.returncode, "stderr": r.stderr.strip()[:500]})
        log.warn(f"Regeneration failed: {' '.join(cmd)} (exit {r.returncode})")
        if r.stderr.strip():
            log.dim(r.stderr.strip()[:200])
        return False

    if stage_dir:
        sr = subprocess.run(["git", "add", "-u"], cwd=regen_dir)
        if sr.returncode != 0:
            _terr("regeneration", f"git add -u failed in {regen_dir}")
            log.warn(f"git add -u failed after regeneration in {Path(regen_dir).name}/")
            return False
    else:
        for f in files:
            if not _git_add(f, cwd):
                return False

    if _trail:
        _trail.info("regeneration", f"regenerated {', '.join(files)}",
                    data={"cmd": cmd, "dir": regen_dir, "mise": use_mise})
    log.ok(f"Regenerated: {', '.join(files)}")
    return True
```

Note: `shutil` is already imported at the top of the file (used by `ai_backend.py` but check `pr-rebase` — if not present, add `import shutil` to the imports section).

- [ ] **Step 4: Verify shutil import exists**

Run: `grep -n "^import shutil" ai/claude/bin/pr-rebase`

If not found, add `import shutil` to the imports block at the top of the file (around line 7, after `import subprocess`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/pr_rebase_test.py -k "detect_mise or run_regeneration" -v`
Expected: all 11 tests PASS

- [ ] **Step 6: Commit**

```
git add ai/claude/bin/pr-rebase tests/pr_rebase_test.py
git commit -m "feat(pr-rebase): add mise detection and regeneration runner"
```

---

### Task 3: Add classification function and refactor `_resolve_file_conflicts`

This is the core refactor. It replaces `_resolve_one`, `_resolve_generated`, the `go_dirs` set, and the post-loop `go mod tidy` block with a unified classify → dispatch → deferred-regeneration flow.

**Files:**
- Modify: `ai/claude/bin/pr-rebase:625-812` (the conflict resolution section)

**Interfaces:**
- Consumes: `_find_regenerator(filepath) -> dict | None`, `_run_regeneration(...)`, `_is_generated_file(...)`, `_detect_delete_conflict(...)`, `_is_binary(...)`, `_accept_theirs_and_stage(...)`, `_resolve_delete_conflict(...)`, `_resolve_single_file(...)`
- Produces: `_classify_conflict(filepath: str, full_path: Path, cwd: str) -> tuple[str, Any]` — returns `(strategy, context)` where strategy is one of: `"regenerate"`, `"accept_theirs"`, `"delete"`, `"binary_error"`, `"ai_merge"`

- [ ] **Step 1: Write the failing tests for `_classify_conflict`**

```python
# ── _classify_conflict ─────────────────────────────────────────────────────


def test_classify_conflict_known_lockfile(tmp_path):
    f = tmp_path / "pnpm-lock.yaml"
    f.write_text("content")
    strategy, ctx = pr_rebase_cli._classify_conflict("pnpm-lock.yaml", f, str(tmp_path))
    assert strategy == "regenerate"
    assert ctx["cmd"] == ["pnpm", "install"]


def test_classify_conflict_go_sum(tmp_path):
    f = tmp_path / "go.sum"
    f.write_text("content")
    strategy, ctx = pr_rebase_cli._classify_conflict("go.sum", f, str(tmp_path))
    assert strategy == "regenerate"
    assert ctx["cmd"] == ["go", "mod", "tidy"]


def test_classify_conflict_generated_file(tmp_path):
    f = tmp_path / "service.pb.go"
    f.write_text("// Code generated. DO NOT EDIT.\npackage v1\n")
    with mock.patch.object(
        pr_rebase_cli, "_is_generated_file", return_value=(True, "header"),
    ):
        strategy, ctx = pr_rebase_cli._classify_conflict("service.pb.go", f, str(tmp_path))
    assert strategy == "accept_theirs"
    assert ctx == "header"


def test_classify_conflict_delete_conflict(tmp_path):
    f = tmp_path / "old.go"
    f.write_text("content")
    with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=(False, "")), \
         mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value="theirs_deleted"):
        strategy, ctx = pr_rebase_cli._classify_conflict("old.go", f, str(tmp_path))
    assert strategy == "delete"
    assert ctx == "theirs_deleted"


def test_classify_conflict_binary_file(tmp_path):
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\x00\x00")
    with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=(False, "")), \
         mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
        strategy, ctx = pr_rebase_cli._classify_conflict("image.png", f, str(tmp_path))
    assert strategy == "binary_error"


def test_classify_conflict_text_file(tmp_path):
    f = tmp_path / "main.go"
    f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")
    with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=(False, "")), \
         mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
        strategy, ctx = pr_rebase_cli._classify_conflict("main.go", f, str(tmp_path))
    assert strategy == "ai_merge"


def test_classify_conflict_lockfile_takes_priority_over_generated(tmp_path):
    """Registry match wins even if the file is also detected as generated."""
    f = tmp_path / "pnpm-lock.yaml"
    f.write_text("content")
    with mock.patch.object(
        pr_rebase_cli, "_is_generated_file", return_value=(True, "gitattributes"),
    ):
        strategy, _ = pr_rebase_cli._classify_conflict("pnpm-lock.yaml", f, str(tmp_path))
    assert strategy == "regenerate"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pr_rebase_test.py::test_classify_conflict_known_lockfile -v`
Expected: FAIL with `AttributeError: module 'pr_rebase_cli' has no attribute '_classify_conflict'`

- [ ] **Step 3: Write `_classify_conflict`**

Add after `_accept_theirs_and_stage` (replacing where `_resolve_one` and `_resolve_generated` currently are):

```python
def _classify_conflict(
    filepath: str, full_path: Path, cwd: str,
) -> tuple[str, Any]:
    """Determine resolution strategy for a conflicted file.

    Returns (strategy, context) where strategy is one of:
      "regenerate"    — accept theirs and run a regeneration command
      "accept_theirs" — accept theirs, no local regeneration
      "delete"        — accept deletion (modify/delete conflict)
      "binary_error"  — cannot resolve binary files
      "ai_merge"      — resolve via AI
    """
    regen = _find_regenerator(filepath)
    if regen is not None:
        return ("regenerate", regen)

    generated, method = _is_generated_file(filepath, full_path, cwd)
    if generated:
        return ("accept_theirs", method)

    delete_type = _detect_delete_conflict(filepath, cwd)
    if delete_type is not None:
        return ("delete", delete_type)

    if _is_binary(full_path):
        return ("binary_error", None)

    return ("ai_merge", None)
```

Add `from typing import Any` to the imports if not already present, or use inline `any` lowercase if on Python 3.10+.

- [ ] **Step 4: Run classification tests to verify they pass**

Run: `pytest tests/pr_rebase_test.py -k "classify_conflict" -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Rewrite `_resolve_file_conflicts` to use the new flow**

Replace the entire `_resolve_file_conflicts` function (lines 767-812) with:

```python
def _resolve_file_conflicts(
    conflicts: list[str], cwd: str, sha: str, subject: str,
) -> list[str] | None:
    """Resolve conflicted files via classify → dispatch → deferred regen."""
    resolved = []
    pending_regens: dict[tuple[str, tuple[str, ...]], tuple[list[str], bool]] = {}

    for filepath in conflicts:
        full_path = Path(cwd) / filepath
        strategy, context = _classify_conflict(filepath, full_path, cwd)

        if strategy == "regenerate":
            if _trail:
                _trail.decision(
                    "regenerate", f"accepting theirs for {filepath}",
                    reason=f"lockfile with known regenerator: {' '.join(context['cmd'])}",
                )
            if not _accept_theirs_and_stage(filepath, full_path.parent, cwd):
                return None
            regen_dir = str(full_path.parent)
            cmd_tuple = tuple(context["cmd"])
            key = (regen_dir, cmd_tuple)
            if key not in pending_regens:
                pending_regens[key] = ([], context.get("stage_dir", False))
            pending_regens[key][0].append(filepath)
            resolved.append(filepath)

        elif strategy == "accept_theirs":
            if _trail:
                _trail.decision(
                    "generated_file", f"accepting theirs for {filepath}",
                    reason=f"generated file detected via {context}",
                )
            if not _accept_theirs_and_stage(filepath, full_path.parent, cwd):
                return None
            resolved.append(filepath)

        elif strategy == "delete":
            if not _resolve_delete_conflict(filepath, sha, cwd, context):
                return None
            resolved.append(filepath)

        elif strategy == "binary_error":
            _terr("resolve_conflicts", f"binary file: {filepath}", data={"filepath": filepath})
            log.error(f"Cannot resolve binary file: {filepath}")
            return None

        elif strategy == "ai_merge":
            result = _resolve_single_file(filepath, full_path, sha, subject, cwd)
            if result is None:
                return None
            if _trail:
                _trail.info("resolve_conflict", f"resolved {filepath}", data={"commit": sha, "method": "ai"})
            resolved.append(filepath)

    for (regen_dir, cmd_tuple), (files, stage_dir) in pending_regens.items():
        _run_regeneration(regen_dir, list(cmd_tuple), files, stage_dir=stage_dir, cwd=cwd)

    return resolved
```

- [ ] **Step 6: Delete `_resolve_one` and `_resolve_generated`**

Remove the `_resolve_one` function (was lines 727-755) and `_resolve_generated` function (was lines 758-764). They are fully replaced by `_classify_conflict` + the dispatch in `_resolve_file_conflicts`.

- [ ] **Step 7: Update `_accept_theirs_and_stage` log message**

Change the log message from `"will regenerate"` to just the file name:

In `_accept_theirs_and_stage`, change:
```python
log.info(f"Accepted theirs: {filepath} (will regenerate)")
```
to:
```python
log.info(f"Accepted theirs: {filepath}")
```

- [ ] **Step 8: Update existing tests that reference deleted functions**

The following tests reference `_resolve_one` directly and need updating:

`test_resolve_one_delete_conflict_skips_ai` — rewrite to test `_classify_conflict` + dispatch:
```python
def test_classify_and_dispatch_delete_conflict():
    """Modify/delete conflict classifies as 'delete' and resolves via git rm."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "KanbanOverlay.tsx"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("some content\n")

        with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=(False, "")), \
             mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value="theirs_deleted"):
            strategy, ctx = pr_rebase_cli._classify_conflict(filepath, full_path, tmpdir)

        assert strategy == "delete"
        assert ctx == "theirs_deleted"
```

`test_resolve_one_normal_conflict_falls_through_to_ai` — rewrite similarly:
```python
def test_classify_normal_conflict_as_ai_merge():
    """Normal content conflict classifies as 'ai_merge'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = "main.go"
        full_path = Path(tmpdir) / filepath
        full_path.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        with mock.patch.object(pr_rebase_cli, "_is_generated_file", return_value=(False, "")), \
             mock.patch.object(pr_rebase_cli, "_detect_delete_conflict", return_value=None):
            strategy, ctx = pr_rebase_cli._classify_conflict(filepath, full_path, tmpdir)

        assert strategy == "ai_merge"
```

- [ ] **Step 9: Update `test_resolve_file_conflicts_handles_go_sum`**

The test should verify go.sum is classified as "regenerate" and `_run_regeneration` is called. Replace with:

```python
def test_resolve_file_conflicts_handles_go_sum():
    with tempfile.TemporaryDirectory() as tmpdir:
        go_sum = Path(tmpdir) / "go.sum"
        go_sum.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(pr_rebase_cli, "_run_regeneration", return_value=True) as mock_regen:
            result = pr_rebase_cli._resolve_file_conflicts(
                ["go.sum"], tmpdir, "abc123", "feat: deps",
            )

        assert result == ["go.sum"]
        cmds = [c[0] for c in calls]
        assert ["git", "checkout", "--theirs", "go.sum"] in cmds
        assert ["git", "add", "go.sum"] in cmds
        mock_regen.assert_called_once()
        regen_args = mock_regen.call_args
        assert regen_args[0][1] == ["go", "mod", "tidy"]
        assert regen_args[1]["stage_dir"] is True
```

- [ ] **Step 10: Update `test_resolve_file_conflicts_go_mod_triggers_tidy`**

Replace with a version that verifies `_run_regeneration` is called instead of raw `go mod tidy`:

```python
def test_resolve_file_conflicts_go_mod_triggers_regeneration():
    with tempfile.TemporaryDirectory() as tmpdir:
        go_mod = Path(tmpdir) / "go.mod"
        go_mod.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        resolved_output = "<<<RESOLVED>>>\nmodule example.com\n<<<END_RESOLVED>>>\n"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=resolved_output, stderr="",
                )
            if cmd[:2] == ["git", "show"] and len(cmd) > 2 and ":2:" in cmd[2]:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="base\n", stderr="")
            if cmd[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="diff\n", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(pr_rebase_cli, "_run_regeneration", return_value=True) as mock_regen:
            result = pr_rebase_cli._resolve_file_conflicts(
                ["go.mod"], tmpdir, "abc123", "feat: deps",
            )

        assert result == ["go.mod"]
        # go.mod is in registry — should be accepted-theirs + regenerated, not AI-merged
        mock_regen.assert_called_once()
        regen_args = mock_regen.call_args
        assert regen_args[0][1] == ["go", "mod", "tidy"]
```

Note: `go.mod` is now in the registry, so it will be accepted-theirs + regenerated instead of AI-merged. This is a behavioral change — the old code AI-merged `go.mod` conflicts and only ran `go mod tidy` after. The new code accepts theirs and regenerates, which is correct because `go mod tidy` rewrites `go.mod` from the dependency tree.

- [ ] **Step 11: Add a new test for pnpm-lock.yaml regeneration**

```python
def test_resolve_file_conflicts_regenerates_pnpm_lockfile():
    """pnpm-lock.yaml is accepted-theirs and regenerated — the original bug case."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lockfile = Path(tmpdir) / "pnpm-lock.yaml"
        lockfile.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n")

        calls = []
        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("cwd")))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(pr_rebase_cli, "_run_regeneration", return_value=True) as mock_regen:
            result = pr_rebase_cli._resolve_file_conflicts(
                ["pnpm-lock.yaml"], tmpdir, "abc123", "feat: deps",
            )

        assert result == ["pnpm-lock.yaml"]
        cmds = [c[0] for c in calls]
        assert ["git", "checkout", "--theirs", "pnpm-lock.yaml"] in cmds
        mock_regen.assert_called_once()
        regen_args = mock_regen.call_args
        assert regen_args[0][1] == ["pnpm", "install"]
        assert regen_args[1].get("stage_dir", False) is False
```

- [ ] **Step 12: Run the full test suite**

Run: `pytest tests/pr_rebase_test.py -v`
Expected: all tests PASS. Watch for failures in tests that previously referenced `_resolve_one` or the old `go_dirs` flow.

- [ ] **Step 13: Commit**

```
git add ai/claude/bin/pr-rebase tests/pr_rebase_test.py
git commit -m "refactor(pr-rebase): replace scattered special cases with classify-dispatch-regen flow"
```

---

### Task 4: Add AI fallback for unknown generated files

**Files:**
- Modify: `ai/claude/bin/pr-rebase` (in `_resolve_file_conflicts`, the `"accept_theirs"` branch)

**Interfaces:**
- Consumes: `ai_backend.prompt(text) -> (str, int)`, `ai_backend.is_available() -> bool`, `_run_regeneration(...)`
- Produces: `_ai_suggest_regeneration(filepath: str, cwd: str) -> list[str] | None` — returns a command list or None

- [ ] **Step 1: Write the failing tests**

```python
# ── _ai_suggest_regeneration ──────────────────────────────────────────────


def test_ai_suggest_regeneration_returns_command(tmp_path):
    subdir = tmp_path / "ui-admin"
    subdir.mkdir()
    (subdir / "package.json").write_text('{"name": "ui-admin"}')
    (subdir / "pnpm-lock.yaml").write_text("lockfile content")

    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("pnpm install", 0)
        result = pr_rebase_cli._ai_suggest_regeneration(
            "ui-admin/generated.css", str(tmp_path),
        )

    assert result == ["pnpm", "install"]


def test_ai_suggest_regeneration_returns_none_response(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("NONE", 0)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_ai_unavailable(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = False
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_ai_fails(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("", 1)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_empty_response(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("", 0)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result is None


def test_ai_suggest_regeneration_multiword_command(tmp_path):
    with mock.patch.object(pr_rebase_cli, "ai_backend") as mock_ai:
        mock_ai.is_available.return_value = True
        mock_ai.prompt.return_value = ("cargo generate-lockfile", 0)
        result = pr_rebase_cli._ai_suggest_regeneration("file.gen", str(tmp_path))

    assert result == ["cargo", "generate-lockfile"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pr_rebase_test.py::test_ai_suggest_regeneration_returns_command -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write the implementation**

Add before `_classify_conflict`:

```python
def _ai_suggest_regeneration(filepath: str, cwd: str) -> list[str] | None:
    """Ask AI to suggest a regeneration command for an unknown generated file.

    Returns a command list or None if unavailable/unparseable.
    """
    if not ai_backend.is_available():
        return None

    target_dir = str(Path(cwd) / Path(filepath).parent)
    try:
        nearby = subprocess.run(
            ["ls", "-1"],
            capture_output=True, text=True, cwd=target_dir,
        ).stdout.strip()
    except Exception:
        nearby = "(unavailable)"

    prompt_text = (
        "A merge conflict was resolved by accepting theirs for a generated file.\n"
        f"File: {filepath}\n"
        f"Project root: {cwd}\n"
        f"Nearby files:\n{nearby}\n\n"
        "What single shell command regenerates this file from its source?\n"
        'Reply with ONLY the command (e.g. "pnpm install"), or "NONE" '
        "if no regeneration is needed.\n"
    )

    stdout, rc = ai_backend.prompt(prompt_text)
    if rc != 0 or not stdout.strip():
        return None

    response = stdout.strip().strip('"').strip("'")
    if response.upper() == "NONE":
        return None

    parts = response.split()
    if not parts:
        return None

    if _trail:
        _trail.info("ai_suggest_regen", f"AI suggested: {response}",
                    data={"filepath": filepath, "command": parts})
    return parts
```

- [ ] **Step 4: Wire the AI fallback into the `"accept_theirs"` branch**

In `_resolve_file_conflicts`, update the `"accept_theirs"` branch to attempt AI-suggested regeneration:

```python
        elif strategy == "accept_theirs":
            if _trail:
                _trail.decision(
                    "generated_file", f"accepting theirs for {filepath}",
                    reason=f"generated file detected via {context}",
                )
            if not _accept_theirs_and_stage(filepath, full_path.parent, cwd):
                return None
            ai_cmd = _ai_suggest_regeneration(filepath, cwd)
            if ai_cmd:
                regen_dir = str(full_path.parent)
                cmd_tuple = tuple(ai_cmd)
                key = (regen_dir, cmd_tuple)
                if key not in pending_regens:
                    pending_regens[key] = ([], False)
                pending_regens[key][0].append(filepath)
            resolved.append(filepath)
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/pr_rebase_test.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```
git add ai/claude/bin/pr-rebase tests/pr_rebase_test.py
git commit -m "feat(pr-rebase): add AI fallback for unknown generated files"
```

---

### Task 5: Verify with full test suite and manual spot-check

**Files:**
- No new code — verification only

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/pr_rebase_test.py -v`
Expected: all tests PASS, no regressions

- [ ] **Step 2: Run shellcheck on the script (if applicable)**

Run: `python3 -c "import ast; ast.parse(open('ai/claude/bin/pr-rebase').read()); print('syntax OK')"` from the repo root
Expected: `syntax OK`

- [ ] **Step 3: Verify no references to deleted functions remain**

Run: `grep -n "_resolve_one\|_resolve_generated\|go_dirs" ai/claude/bin/pr-rebase`
Expected: no output (all references removed)

- [ ] **Step 4: Verify the registry covers the original bug case**

Run: `python3 -c "import importlib.machinery, importlib.util; l=importlib.machinery.SourceFileLoader('m','ai/claude/bin/pr-rebase'); s=importlib.util.spec_from_loader('m',l); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); r=m._find_regenerator('pnpm-lock.yaml'); print(f'pnpm-lock.yaml -> {r}'); assert r and r['cmd']==['pnpm','install']"`
Expected: `pnpm-lock.yaml -> {'cmd': ['pnpm', 'install']}`

- [ ] **Step 5: Commit (if any fixes were needed)**

Only if steps 1-4 revealed issues that required fixes.
