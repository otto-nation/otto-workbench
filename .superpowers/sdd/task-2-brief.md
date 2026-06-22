### Task 2: Add cmd_rebase to pr CLI

**Files:**
- Modify: `ai/claude/bin/pr`
- Modify: `tests/pr_cli_test.py`

**Interfaces:**
- Consumes: `pr_context.ResolvedContext`, `pr_state.RebaseSummary`, `pr_state.update_rebase`, `pr_state.save_state`, `_load_or_init`, `_now_iso`
- Produces: `cmd_rebase(args, extra, ctx)` function, `_detect_rebase_in_progress(cwd)` helper, `_detect_conflicts(cwd)` helper, `_render_rebase_section(r)` function; argparse subparser `rebase` with `--fix`, `--push`, `--abort` flags; dispatch entry `"rebase": cmd_rebase`

- [ ] **Step 1: Write failing tests for rebase helpers**

Add tests to `tests/pr_cli_test.py`. These test the pure helper functions, not the full `cmd_rebase` (which requires git state):

```python
# ── _render_rebase_section ────────────────────────────────────────────────


def test_render_rebase_section_not_run():
    import pr_state
    r = pr_state.RebaseSummary()
    result = pr_cli._render_rebase_section(r)
    assert result == ["**Rebase**: not run yet"]


def test_render_rebase_section_with_data():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert len(result) >= 1
    assert "2 file(s)" in result[0] or "2" in result[0]
    assert "3 commit(s)" in result[0] or "3" in result[0]
    assert "force-pushed" in result[0]


def test_render_rebase_section_no_conflicts():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=5,
        conflicts_resolved=0, files_resolved=[],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert len(result) >= 1
    assert "clean" in result[0].lower() or "0" in result[0]


def test_render_rebase_section_not_pushed():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["a.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert "force-pushed" not in result[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pr_cli_test.py::test_render_rebase_section_not_run -v`
Expected: FAIL — `AttributeError: module 'pr_cli' has no attribute '_render_rebase_section'`

- [ ] **Step 3: Implement _render_rebase_section in pr CLI**

Add after `_render_triage_section` in `ai/claude/bin/pr`:

```python
def _render_rebase_section(r: pr_state.RebaseSummary) -> list[str]:
    if not r.updated_at:
        return ["**Rebase**: not run yet"]
    if r.conflicts_resolved == 0:
        desc = f"clean rebase — {r.commits_replayed} commit(s) replayed"
    else:
        desc = f"resolved {r.conflicts_resolved} file(s) across {r.commits_replayed} commit(s)"
    if r.force_pushed:
        desc += ", force-pushed"
    return [f"**Rebase**: {desc}"]
```

- [ ] **Step 4: Run render tests to verify they pass**

Run: `pytest tests/pr_cli_test.py -k "render_rebase" -v`
Expected: all PASS

- [ ] **Step 5: Implement cmd_rebase and helpers**

Add the rebase helpers and command function in `ai/claude/bin/pr`, after the `cmd_gc` function:

```python
def _detect_rebase_in_progress(cwd: str) -> bool:
    """Check if a rebase is currently in progress."""
    git_dir = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True, text=True, cwd=cwd,
    ).stdout.strip()
    git_path = Path(git_dir) if Path(git_dir).is_absolute() else Path(cwd) / git_dir
    return (git_path / "rebase-merge").is_dir() or (git_path / "rebase-apply").is_dir()


def _detect_conflicts(cwd: str) -> list[str]:
    """Return list of conflicted file paths."""
    r = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True, cwd=cwd,
    )
    return [f for f in r.stdout.strip().splitlines() if f]


def _rebase_head_info(cwd: str) -> tuple[str, str]:
    """Return (short SHA, subject) of the commit being rebased."""
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "REBASE_HEAD"],
        capture_output=True, text=True, cwd=cwd,
    ).stdout.strip()
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s", "REBASE_HEAD"],
        capture_output=True, text=True, cwd=cwd,
    ).stdout.strip()
    return sha, subject


def _remaining_rebase_commits(cwd: str) -> int:
    """Count remaining commits in the rebase todo."""
    git_dir = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True, text=True, cwd=cwd,
    ).stdout.strip()
    git_path = Path(git_dir) if Path(git_dir).is_absolute() else Path(cwd) / git_dir
    todo = git_path / "rebase-merge" / "git-rebase-todo"
    if not todo.exists():
        return 0
    return sum(1 for line in todo.read_text().splitlines()
               if line.strip() and not line.startswith("#"))


def cmd_rebase(args, extra, ctx: pr_context.ResolvedContext) -> int:
    """AI-assisted rebase onto origin/main with force-push."""
    cwd = str(ctx.worktree_root)

    # Handle --abort
    if args.abort:
        print("Aborting rebase...", file=sys.stderr)
        r = subprocess.run(["git", "rebase", "--abort"], cwd=cwd)
        if r.returncode == 0:
            state = _load_or_init(ctx)
            pr_state.update_rebase(state, pr_state.RebaseSummary())
            pr_state.save_state(ctx.worktree_root, state)
            print("Rebase aborted.", file=sys.stderr)
        return r.returncode

    # Handle --push
    if args.push:
        if _detect_rebase_in_progress(cwd):
            print("Cannot push — rebase still in progress.", file=sys.stderr)
            return 1
        print("Force-pushing...", file=sys.stderr)
        r = subprocess.run(
            ["git", "push", "--force-with-lease"],
            cwd=cwd,
        )
        if r.returncode == 0:
            state = _load_or_init(ctx)
            state.rebase.force_pushed = True
            state.rebase.updated_at = _now_iso()
            pr_state.save_state(ctx.worktree_root, state)
            print("Force-pushed successfully.", file=sys.stderr)
        return r.returncode

    # Pre-flight: not on main
    branch = ctx.branch
    if branch in ("main", "master"):
        print("Cannot rebase — currently on protected branch.", file=sys.stderr)
        return 1

    # Check for in-progress rebase
    resuming = _detect_rebase_in_progress(cwd)

    if resuming:
        print("Detected in-progress rebase — resuming...", file=sys.stderr)
        conflicts = _detect_conflicts(cwd)
        if conflicts:
            sha, subject = _rebase_head_info(cwd)
            remaining = _remaining_rebase_commits(cwd)
            report = {
                "status": "conflicts_resuming",
                "files": conflicts,
                "rebase_head": sha,
                "rebase_head_subject": subject,
                "remaining_commits": remaining,
            }
            json.dump(report, sys.stdout, indent=2)
            print()
            return 3
        # In-progress but no conflicts — try continuing
        r = subprocess.run(
            ["git", "-c", "core.editor=true", "rebase", "--continue"],
            cwd=cwd,
        )
        if r.returncode == 0:
            return _rebase_success(args, ctx, cwd)
        # Continue hit more conflicts
        conflicts = _detect_conflicts(cwd)
        if conflicts:
            sha, subject = _rebase_head_info(cwd)
            remaining = _remaining_rebase_commits(cwd)
            report = {
                "status": "conflicts",
                "files": conflicts,
                "rebase_head": sha,
                "rebase_head_subject": subject,
                "remaining_commits": remaining,
            }
            json.dump(report, sys.stdout, indent=2)
            print()
            return 3
        return r.returncode

    # Pre-flight: clean working tree
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=cwd,
    )
    if status.stdout.strip():
        print("Cannot rebase — working tree has uncommitted changes.", file=sys.stderr)
        return 1

    # Fetch
    print("Fetching origin...", file=sys.stderr)
    subprocess.run(["git", "fetch", "origin"], cwd=cwd)

    # Run rebase
    print("Rebasing onto origin/main...", file=sys.stderr)
    r = subprocess.run(["git", "rebase", "origin/main"], cwd=cwd)

    if r.returncode == 0:
        return _rebase_success(args, ctx, cwd)

    # Conflicts
    conflicts = _detect_conflicts(cwd)
    if conflicts:
        sha, subject = _rebase_head_info(cwd)
        remaining = _remaining_rebase_commits(cwd)
        report = {
            "status": "conflicts",
            "files": conflicts,
            "rebase_head": sha,
            "rebase_head_subject": subject,
            "remaining_commits": remaining,
        }
        json.dump(report, sys.stdout, indent=2)
        print()
        return 3

    return r.returncode


def _rebase_success(args, ctx: pr_context.ResolvedContext, cwd: str) -> int:
    """Handle clean rebase completion — update state and force-push."""
    # Count commits replayed
    commits_ahead = subprocess.run(
        ["git", "rev-list", "--count", "origin/main..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    ).stdout.strip()

    state = _load_or_init(ctx)

    if args.fix:
        # Autonomous mode: force-push immediately
        print("Rebase clean — force-pushing...", file=sys.stderr)
        r = subprocess.run(
            ["git", "push", "--force-with-lease"],
            cwd=cwd,
        )
        pr_state.update_rebase(state, pr_state.RebaseSummary(
            target_base="origin/main",
            commits_replayed=int(commits_ahead) if commits_ahead.isdigit() else 0,
            conflicts_resolved=0,
            files_resolved=[],
            force_pushed=r.returncode == 0,
            updated_at=_now_iso(),
        ))
        pr_state.save_state(ctx.worktree_root, state)
        if r.returncode == 0:
            print("Rebase complete — force-pushed.", file=sys.stderr)
        return r.returncode
    else:
        # Default mode: report success, let skill/user trigger --push
        pr_state.update_rebase(state, pr_state.RebaseSummary(
            target_base="origin/main",
            commits_replayed=int(commits_ahead) if commits_ahead.isdigit() else 0,
            conflicts_resolved=0,
            files_resolved=[],
            force_pushed=False,
            updated_at=_now_iso(),
        ))
        pr_state.save_state(ctx.worktree_root, state)
        report = {
            "status": "clean",
            "commits_replayed": int(commits_ahead) if commits_ahead.isdigit() else 0,
        }
        json.dump(report, sys.stdout, indent=2)
        print()
        print("Rebase clean — run `pr rebase --push` to force-push.", file=sys.stderr)
        return 0
```

- [ ] **Step 6: Add argparse subparser and dispatch entry**

In the `main()` function, add after the `pr gc` subparser:

```python
    # pr rebase
    rebase_p = sub.add_parser("rebase", help="Rebase onto origin/main with AI conflict resolution")
    rebase_p.add_argument("--fix", action="store_true",
                          help="Autonomous mode — resolve conflicts and force-push without confirmation")
    rebase_p.add_argument("--push", action="store_true",
                          help="Force-push after successful rebase")
    rebase_p.add_argument("--abort", action="store_true",
                          help="Abort in-progress rebase")
```

Add `"rebase": cmd_rebase` to the dispatch dict.

- [ ] **Step 7: Add rebase to cmd_status rendering**

In `cmd_status`, add rebase section rendering after the triage section. Add this after `lines += _render_triage_section(state.triage)`:

```python
    lines.append("")
    lines += _render_rebase_section(state.rebase)
```

- [ ] **Step 8: Update docstring**

Add `pr rebase` to the module docstring at the top of the file:

```
  pr rebase     Rebase onto origin/main with AI conflict resolution
```

- [ ] **Step 9: Run all tests**

Run: `pytest tests/pr_cli_test.py tests/pr_state_test.py -v`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add ai/claude/bin/pr tests/pr_cli_test.py
git commit -m "feat(pr): add rebase subcommand with conflict detection and force-push"
```

---

