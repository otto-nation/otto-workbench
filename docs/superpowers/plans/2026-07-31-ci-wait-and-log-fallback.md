# CI Wait Mode and Log Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--wait` flag to `ci-check` for polling in-progress CI runs with incremental failure reporting, and fix log fallback so generic annotation messages always trigger log fetching.

**Architecture:** Two independent changes to `ci-check`. The log-fallback fix changes `_annotations_uninformative()` and expands `_GENERIC_MESSAGES`. The wait mode adds a new `_run_ci_wait()` function with a poll loop that emits partial JSON reports (delimited by `---`) to stdout and status lines to stderr. `_run_ci()` stays unchanged for the non-wait path. SKILL.md gets updated guidance for `--wait` output.

**Tech Stack:** Python 3 (ci-check script), pytest (tests)

## Global Constraints

- `ci-check` has no shebang-level dependencies beyond Python stdlib — keep it that way
- The `pr` wrapper passes unknown flags through to delegates via `cmd += list(argv)` — no wrapper changes needed
- Tests use `importlib` to load `ci-check` as a module (it has no `.py` extension) — follow this pattern
- All JSON output goes to stdout, all human-readable output goes to stderr
- Backward compatibility: without `--wait`, output must be identical to today

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `ai/claude/bin/ci-check` | Modify | New CLI flags, `_count_job_states()`, `_run_ci_wait()`, expanded `_GENERIC_MESSAGES`, fixed `_annotations_uninformative()` |
| `ai/claude/lib/ci_failures.py` | Modify | `render_dashboard()` status suffix in header |
| `ai/claude/skills/ci-failures/SKILL.md` | Modify | `--wait` guidance in Steps 1-2 |
| `tests/ci_check_test.py` | Modify | Tests for log-fallback fix, `_count_job_states()`, `_run_ci_wait()` |
| `tests/ci_failures_test.py` | Modify | Tests for dashboard status suffix |

---

### Task 1: Fix log-fallback — expand `_GENERIC_MESSAGES` and flip `_annotations_uninformative()` logic

**Files:**
- Modify: `ai/claude/bin/ci-check:195-212` (`_GENERIC_MESSAGES` and `_annotations_uninformative()`)
- Test: `tests/ci_check_test.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `_annotations_uninformative(annotations: list[dict]) -> bool` — same signature, changed behavior (generic messages are now uninformative regardless of path)

- [ ] **Step 1: Write failing test — generic message with source path is uninformative**

The existing test `test_uninformative_generic_exit_code_message` already asserts this behavior. Verify it passes — this confirms the current logic already handles the basic case. Then add a new test for the expanded messages:

```python
def test_uninformative_exited_with_code():
    """'exited with code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Step exited with code 1", "path": "src/main.go", "start_line": 1},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_returned_non_zero():
    """'returned a non-zero code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Command returned a non-zero code: 2", "path": "Makefile", "start_line": 10},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_check_failure_on_line():
    """'check failure on line' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Check failure on line 42", "path": ".github/workflows/ci.yml", "start_line": 42},
    ]
    assert ci_check._annotations_uninformative(annotations) is True


def test_uninformative_failed_with_exit_code():
    """'failed with exit code' variant is also uninformative."""
    annotations = [
        {"annotation_level": "failure", "message": "Job failed with exit code 1", "path": ".github/workflows/ci.yml", "start_line": 1},
    ]
    assert ci_check._annotations_uninformative(annotations) is True
```

- [ ] **Step 2: Run tests to verify new ones fail**

Run: `pytest tests/ci_check_test.py::test_uninformative_exited_with_code tests/ci_check_test.py::test_uninformative_returned_non_zero tests/ci_check_test.py::test_uninformative_check_failure_on_line tests/ci_check_test.py::test_uninformative_failed_with_exit_code -v`

Expected: FAIL — current `_GENERIC_MESSAGES` only has `"process completed with exit code"` and the logic requires both path-missing AND generic-message.

- [ ] **Step 3: Expand `_GENERIC_MESSAGES` and fix `_annotations_uninformative()`**

In `ai/claude/bin/ci-check`, replace lines 195-212:

```python
_GENERIC_MESSAGES = (
    "process completed with exit code",
    "a]process completed with exit code",
    "check failure on line",
    "failed with exit code",
    "exited with code",
    "returned a non-zero code",
)


def _annotations_uninformative(annotations: list[dict]) -> bool:
    """True when non-notice annotations carry no actionable diagnostic context.

    A generic message (exit code restated, check failure marker) is always
    uninformative regardless of path — the real error is in the logs.
    """
    non_notices = [a for a in annotations if a.get("annotation_level") != "notice"]
    if not non_notices:
        return True
    for a in non_notices:
        msg = (a.get("message", "") or "").lower()
        if any(g in msg for g in _GENERIC_MESSAGES):
            continue
        path = a.get("path", "")
        has_source_path = bool(path) and "." in path.rsplit("/", 1)[-1]
        if has_source_path:
            return False
    return True
```

- [ ] **Step 4: Run all `_annotations_uninformative` tests**

Run: `pytest tests/ci_check_test.py -k "uninformative or informative" -v`

Expected: ALL PASS — both new and existing tests.

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `pytest tests/ci_check_test.py tests/ci_failures_test.py -v`

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add ai/claude/bin/ci-check tests/ci_check_test.py
git commit -m "fix(ci-check): generic annotation messages always trigger log fallback"
```

---

### Task 2: Add `_count_job_states()` helper

**Files:**
- Modify: `ai/claude/bin/ci-check` (add new function after `_merge_runs()`)
- Test: `tests/ci_check_test.py`

**Interfaces:**
- Consumes: merged run data dict from `_merge_runs()` — specifically `merged["jobs"]`, each job having `"status"` and `"conclusion"` keys
- Produces: `_count_job_states(merged: dict) -> tuple[int, int, int, int]` returning `(completed, failed, running, queued)` where `completed` is total finished jobs (pass + fail + neutral), `failed` is the subset of completed with failure conclusions, `running` is in-progress, `queued` is waiting/pending/queued. Used by Task 4's `_run_ci_wait()` for status lines.

- [ ] **Step 1: Write failing tests**

```python
def test_count_job_states_all_completed():
    merged = {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "completed", "conclusion": "failure"},
        {"name": "build", "status": "completed", "conclusion": "neutral"},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 3
    assert failed == 1
    assert running == 0
    assert queued == 0


def test_count_job_states_mixed():
    merged = {"jobs": [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "test", "status": "in_progress", "conclusion": None},
        {"name": "build", "status": "queued", "conclusion": None},
        {"name": "deploy", "status": "waiting", "conclusion": None},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 1
    assert failed == 0
    assert running == 1
    assert queued == 2


def test_count_job_states_empty():
    merged = {"jobs": []}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 0
    assert failed == 0
    assert running == 0
    assert queued == 0


def test_count_job_states_timed_out_is_failed():
    merged = {"jobs": [
        {"name": "slow", "status": "completed", "conclusion": "timed_out"},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert completed == 1
    assert failed == 1


def test_count_job_states_pending_is_queued():
    merged = {"jobs": [
        {"name": "deploy", "status": "pending", "conclusion": None},
    ]}
    completed, failed, running, queued = ci_check._count_job_states(merged)
    assert queued == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ci_check_test.py::test_count_job_states_all_completed -v`

Expected: FAIL — `_count_job_states` does not exist.

- [ ] **Step 3: Implement `_count_job_states()`**

Add after `_merge_runs()` in `ai/claude/bin/ci-check`:

```python
def _count_job_states(merged: dict) -> tuple[int, int, int, int]:
    """Count jobs by state: (completed, failed, running, queued)."""
    completed = failed = running = queued = 0
    for job in merged.get("jobs", []):
        status = job.get("status", "")
        conclusion = job.get("conclusion")
        if status == "completed":
            completed += 1
            if conclusion in _FAILURE_CONCLUSIONS:
                failed += 1
        elif status == "in_progress":
            running += 1
        else:
            queued += 1
    return completed, failed, running, queued
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/ci_check_test.py -k "count_job_states" -v`

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add ai/claude/bin/ci-check tests/ci_check_test.py
git commit -m "feat(ci-check): add _count_job_states helper for wait mode"
```

---

### Task 3: Add dashboard status suffix

**Files:**
- Modify: `ai/claude/lib/ci_failures.py:346-363` (`render_dashboard()`)
- Test: `tests/ci_failures_test.py`

**Interfaces:**
- Consumes: nothing new — `render_dashboard()` already receives `run: RunState` which has `run.status`
- Produces: `render_dashboard(run, progression, run_ids=None, show_status=False) -> str` — new optional `show_status` parameter. When `True`, appends ` — in progress` or ` — complete` to the header line. Used by Task 4's `_run_ci_wait()`.

- [ ] **Step 1: Write failing tests**

Add to `tests/ci_failures_test.py`:

```python
def test_render_dashboard_show_status_in_progress():
    item = _make_item("a")
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="in_progress", conclusion="",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, show_status=True)
    assert "— in progress" in dashboard
    assert "Run #5" in dashboard


def test_render_dashboard_show_status_complete():
    item = _make_item("a")
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    dashboard = render_dashboard(run, {"a": Outcome.NEW}, show_status=True)
    assert "— complete" in dashboard


def test_render_dashboard_show_status_default_off():
    """Without show_status, header has no status suffix."""
    run = RunState(
        run_id=100, run_number=5, head_sha="abc1234",
        status="in_progress", conclusion="",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={},
    )
    dashboard = render_dashboard(run, {})
    assert "— in progress" not in dashboard
    assert "— complete" not in dashboard
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ci_failures_test.py::test_render_dashboard_show_status_in_progress -v`

Expected: FAIL — `show_status` parameter not accepted.

- [ ] **Step 3: Add `show_status` parameter to `render_dashboard()`**

In `ai/claude/lib/ci_failures.py`, modify the `render_dashboard()` signature and header line:

```python
def render_dashboard(
    run: RunState,
    progression: dict[str, Outcome],
    run_ids: list[int] | None = None,
    show_status: bool = False,
) -> str:
    """Render a human-readable dashboard string for stderr output."""
    header = f"## CI Run #{run.run_number} ({run.head_sha[:7]})"
    if show_status:
        suffix = "in progress" if run.status != "completed" else "complete"
        header += f" — {suffix}"
    lines = [header, ""]
```

Replace the first two lines of the function body (the old `lines = [...]` and empty string append).

- [ ] **Step 4: Run tests**

Run: `pytest tests/ci_failures_test.py -k "show_status" -v`

Expected: ALL PASS

- [ ] **Step 5: Run full ci_failures test suite**

Run: `pytest tests/ci_failures_test.py -v`

Expected: ALL PASS — existing tests should not be affected since `show_status` defaults to `False`.

- [ ] **Step 6: Commit**

```bash
git add ai/claude/lib/ci_failures.py tests/ci_failures_test.py
git commit -m "feat(ci_failures): add show_status option to render_dashboard"
```

---

### Task 4: Implement `--wait` flag and `_run_ci_wait()` poll loop

**Files:**
- Modify: `ai/claude/bin/ci-check:767-806` (argparse in `main()`) and add `_run_ci_wait()` function
- Test: `tests/ci_check_test.py`

**Interfaces:**
- Consumes:
  - `_fetch_latest_run_ids(repo, branch) -> list[int]` (existing)
  - `_fetch_run_data(repo, run_id) -> dict | None` (existing)
  - `_merge_runs(run_data_list) -> dict | None` (existing)
  - `_parse_run(repo, merged) -> RunState` (existing — used only for newly-failed jobs subset)
  - `_count_job_states(merged) -> tuple[int, int, int, int]` (from Task 2)
  - `ci.render_dashboard(run, progression, run_ids, show_status)` (from Task 3)
  - `ci.compute_progression()`, `ci.sync_ci_domain()` (existing)
- Produces: `_run_ci_wait(trail, args, ctx) -> dict` — same return type as `_run_ci()`. Emits `---`-delimited JSON reports to stdout, status lines to stderr. Called from `main()` when `args.wait` is set.

- [ ] **Step 1: Write test for partial report emission**

```python
def test_run_ci_wait_emits_partial_on_new_failure(capsys):
    """When a job fails during polling, a partial JSON report is emitted."""
    # First poll: one job running, one failed
    run_data_cycle1 = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "in_progress", "conclusion": "failure",
        "jobs": [
            {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
            {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
        ],
    }
    # Second poll: all complete
    run_data_cycle2 = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "completed", "conclusion": "failure",
        "jobs": [
            {"name": "Lint", "conclusion": "failure", "databaseId": 10, "status": "completed"},
            {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
        ],
    }
    fetch_calls = iter([
        ([100], run_data_cycle1),
        ([100], run_data_cycle2),
    ])

    def mock_fetch_ids(repo, branch):
        ids, _ = next(fetch_calls)
        return ids

    call_count = [0]
    def mock_fetch_data(repo, run_id):
        cycle = call_count[0]
        call_count[0] += 1
        if cycle == 0:
            return run_data_cycle1
        return run_data_cycle2

    mock_trail = MagicMock()
    mock_args = MagicMock()
    mock_args.wait_timeout = 120
    mock_args.wait_interval = 0  # no sleep in tests
    mock_args.run = None

    mock_ctx = MagicMock()
    mock_ctx.repo = "owner/repo"
    mock_ctx.branch = "feat/test"
    mock_ctx.pr_number = None
    mock_ctx.worktree_root = None

    with patch("ci_check._fetch_latest_run_ids", side_effect=mock_fetch_ids), \
         patch("ci_check._fetch_run_data", side_effect=mock_fetch_data), \
         patch("ci_check._fetch_annotations", return_value=[]), \
         patch("ci_check._log_fallback", return_value=([], [], ci_check.ci.FailureKind.BUILD)), \
         patch("ci_check._commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        result = ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

    stdout = capsys.readouterr().out
    assert "---" in stdout
    chunks = [c.strip() for c in stdout.split("---") if c.strip()]
    assert len(chunks) >= 1
    partial = json.loads(chunks[0])
    assert partial["type"] == "partial"
    final = json.loads(chunks[-1])
    assert final["type"] == "final"
```

- [ ] **Step 2: Write test for status line emission**

```python
def test_run_ci_wait_emits_status_lines(capsys):
    """Status lines showing job counts appear on stderr."""
    run_data = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "completed", "conclusion": "success",
        "jobs": [
            {"name": "Lint", "conclusion": "success", "databaseId": 10, "status": "completed"},
            {"name": "Test", "conclusion": "success", "databaseId": 11, "status": "completed"},
        ],
    }
    mock_trail = MagicMock()
    mock_args = MagicMock()
    mock_args.wait_timeout = 120
    mock_args.wait_interval = 0
    mock_args.run = None

    mock_ctx = MagicMock()
    mock_ctx.repo = "owner/repo"
    mock_ctx.branch = "feat/test"
    mock_ctx.pr_number = None
    mock_ctx.worktree_root = None

    with patch("ci_check._fetch_latest_run_ids", return_value=[100]), \
         patch("ci_check._fetch_run_data", return_value=run_data), \
         patch("ci_check._commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

    stderr = capsys.readouterr().err
    assert "2/2" in stderr or "complete" in stderr.lower()
```

- [ ] **Step 3: Write test for timeout behavior**

```python
def test_run_ci_wait_times_out(capsys):
    """When timeout is reached, a final report is emitted with whatever we have."""
    run_data = {
        "databaseId": 100, "number": 1, "headSha": "abc123",
        "status": "in_progress", "conclusion": "",
        "jobs": [
            {"name": "Test", "conclusion": None, "databaseId": 11, "status": "in_progress"},
        ],
    }
    mock_trail = MagicMock()
    mock_args = MagicMock()
    mock_args.wait_timeout = 0  # immediate timeout
    mock_args.wait_interval = 0
    mock_args.run = None

    mock_ctx = MagicMock()
    mock_ctx.repo = "owner/repo"
    mock_ctx.branch = "feat/test"
    mock_ctx.pr_number = None
    mock_ctx.worktree_root = None

    with patch("ci_check._fetch_latest_run_ids", return_value=[100]), \
         patch("ci_check._fetch_run_data", return_value=run_data), \
         patch("ci_check._commits_behind_main", return_value=0), \
         patch("ci_check.time.sleep"):
        result = ci_check._run_ci_wait(mock_trail, mock_args, mock_ctx)

    stderr = capsys.readouterr().err
    assert "timeout" in stderr.lower() or "Timeout" in stderr

    assert result["type"] == "final"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/ci_check_test.py::test_run_ci_wait_emits_partial_on_new_failure -v`

Expected: FAIL — `_run_ci_wait` does not exist.

- [ ] **Step 5: Add `import time` and CLI flags to `main()`**

Add `import time` to the imports at the top of `ci-check`.

In `main()`, add arguments after the `--fix` line:

```python
parser.add_argument("--wait", action="store_true",
                    help="Poll until all jobs complete, emitting incremental reports")
parser.add_argument("--wait-timeout", type=int, default=900,
                    help="Max wait time in seconds (default: 900)")
parser.add_argument("--wait-interval", type=int, default=30,
                    help="Poll interval in seconds (default: 30)")
```

In the `main()` body, add the branch before `report = _run_ci(...)`:

```python
if args.wait:
    report = _run_ci_wait(trail, args, ctx)
else:
    report = _run_ci(trail, args, ctx)
```

- [ ] **Step 6: Implement `_run_ci_wait()`**

Add before `main()` in `ci-check`:

```python
def _emit_json(report: dict, report_type: str) -> None:
    """Emit a JSON report to stdout with --- delimiter."""
    report["type"] = report_type
    print("---", flush=True)
    json.dump(report, sys.stdout, indent=2)
    print(flush=True)


def _run_ci_wait(trail, args, ctx) -> dict:
    """Poll CI until all jobs complete, emitting partial reports as failures arrive."""
    repo = ctx.repo
    branch = ctx.branch
    pr_number = ctx.pr_number
    toplevel = ctx.worktree_root

    reported_job_ids: set[int] = set()
    all_partial_failures: list[dict] = []
    start_time = time.monotonic()
    timeout = args.wait_timeout
    interval = args.wait_interval
    merged = None

    while True:
        elapsed = time.monotonic() - start_time

        # Fetch current state
        if args.run:
            run_ids = [args.run]
        else:
            run_ids = _fetch_latest_run_ids(repo, branch)
            if not run_ids:
                trail.warn("no_runs", "no workflow runs found")
                log.error(f"No workflow runs found for branch '{branch}'")
                sys.exit(1)

        with ThreadPoolExecutor(max_workers=5) as pool:
            fetched = list(pool.map(lambda rid: (rid, _fetch_run_data(repo, rid)), run_ids))
        run_data_list = []
        for rid, rd in fetched:
            if rd is not None:
                rd["_run_id"] = rid
                run_data_list.append(rd)

        merged = _merge_runs(run_data_list)
        if merged is None:
            trail.error("fetch_run_data", "failed to fetch run data")
            log.error("Failed to fetch run data")
            sys.exit(1)

        # Find newly-failed jobs
        new_failed_jobs = [
            j for j in merged.get("jobs", [])
            if j.get("conclusion") in _FAILURE_CONCLUSIONS
            and j.get("databaseId", 0) not in reported_job_ids
        ]

        # Process and emit partial report for new failures
        if new_failed_jobs:
            partial_merged = dict(merged)
            partial_merged["jobs"] = new_failed_jobs
            partial_run = _parse_run(repo, partial_merged)

            completed, failed, running, queued = _count_job_states(merged)
            total = completed + running + queued

            partial_report = {
                "completed": completed,
                "total": total,
                "failures": [],
                "progression": {},
            }
            for group_key, group in partial_run.failures.items():
                for item in group.items:
                    partial_report["failures"].append({
                        "id": item.id,
                        "job": group.job,
                        "failed_step": group.failed_step,
                        "kind": group.kind.value,
                        "annotation": item.annotation,
                        "headline": item.headline,
                        "file": item.file,
                        "line": item.line,
                        "diagnosis": item.diagnosis,
                        "fix_sha": item.fix_sha,
                        "outcome": "new",
                        "source_run_id": item.source_run_id,
                        "context": item.context,
                    })

            if partial_report["failures"]:
                _emit_json(partial_report, "partial")
                all_partial_failures.extend(partial_report["failures"])
                trail.info("partial_report", f"{len(partial_report['failures'])} new failure(s)")

            reported_job_ids.update(j.get("databaseId", 0) for j in new_failed_jobs)

        # Status line and dashboard
        completed, failed, running, queued = _count_job_states(merged)
        total = completed + running + queued
        status_parts = [f"{completed}/{total} jobs complete"]
        if running:
            status_parts.append(f"{running} running")
        if queued:
            status_parts.append(f"{queued} queued")
        if failed:
            status_parts.append(f"{failed} failed")

        is_complete = merged.get("status") == "completed" or running + queued == 0

        if is_complete:
            print(f"  {', '.join(status_parts)}", file=sys.stderr, flush=True)
        else:
            print(f"  {', '.join(status_parts)}", file=sys.stderr, flush=True)

        if is_complete:
            break

        if elapsed >= timeout:
            print(f"  Timeout after {int(elapsed)}s — emitting partial results", file=sys.stderr, flush=True)
            trail.warn("wait_timeout", f"timed out after {int(elapsed)}s")
            break

        time.sleep(interval)

    # Final full report — reuse _run_ci() logic for the complete state
    run_state = _parse_run(repo, merged)

    if toplevel:
        state = pr_state.load_or_init(
            worktree_root=toplevel, repo=repo, branch=branch,
            pr_number=pr_number, head_sha=run_state.head_sha,
        )
        ci_domain = state.ci
    else:
        state = None
        ci_domain = pr_state.CIDomain()

    prior_run = ci_domain.runs.get(str(ci_domain.latest_run_id)) if ci_domain.latest_run_id else None
    prior_failures = prior_run.failures if prior_run else {}
    progression = ci.compute_progression(run_state.failures, prior_failures)

    ci.sync_ci_domain(ci_domain, run_state)

    dashboard = ci.render_dashboard(run_state, progression, run_ids=run_ids, show_status=True)
    print(dashboard, file=sys.stderr)

    behind_main = _commits_behind_main(repo, branch) if branch not in ("main", "master") else 0

    report = {
        "repo": repo,
        "branch": branch,
        "pr_number": pr_number,
        "run_id": run_state.run_id,
        "run_ids": run_ids,
        "run_number": run_state.run_number,
        "head_sha": run_state.head_sha,
        "conclusion": run_state.conclusion,
        "behind_main": behind_main,
        "failures": [],
        "progression": {k: v.value for k, v in progression.items()},
        "resolved_since_prior": [],
        "completed": completed,
        "total": total,
    }

    for group_key, group in run_state.failures.items():
        for item in group.items:
            report["failures"].append({
                "id": item.id,
                "job": group.job,
                "failed_step": group.failed_step,
                "kind": group.kind.value,
                "annotation": item.annotation,
                "headline": item.headline,
                "file": item.file,
                "line": item.line,
                "diagnosis": item.diagnosis,
                "fix_sha": item.fix_sha,
                "outcome": progression.get(item.id, ci.Outcome.NEW).value,
                "source_run_id": item.source_run_id,
                "context": item.context,
            })

    if prior_run:
        prior_item_ids = ci.collect_item_ids(prior_run.failures)
        current_item_ids = ci.collect_item_ids(run_state.failures)
        report["resolved_since_prior"] = [
            item_id for item_id in prior_item_ids
            if item_id not in current_item_ids
        ]

    if state is not None:
        kind_counts: dict[str, int] = {}
        for f in report.get("failures", []):
            k = f.get("kind", "unknown")
            kind_counts[k] = kind_counts.get(k, 0) + 1
        ci_domain.conclusion = run_state.conclusion
        ci_domain.failure_count = len(report.get("failures", []))
        ci_domain.failure_kinds = kind_counts
        ci_domain.last_run_id = run_state.run_id
        ci_domain.last_run_number = run_state.run_number
        ci_domain.updated_at = pr_state.now_iso()
        try:
            pr_state.save_state(toplevel, state)
        except Exception as exc:
            trail.error("state_update", f"state update failed: {exc}")
            log.error(f"ci-check: state update failed: {exc}")

    _emit_json(report, "final")

    return report
```

- [ ] **Step 7: Run wait mode tests**

Run: `pytest tests/ci_check_test.py -k "run_ci_wait" -v`

Expected: ALL PASS

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ci_check_test.py tests/ci_failures_test.py -v`

Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add ai/claude/bin/ci-check tests/ci_check_test.py
git commit -m "feat(ci-check): add --wait flag with incremental failure reporting"
```

---

### Task 5: Update SKILL.md for `--wait` guidance

**Files:**
- Modify: `ai/claude/skills/ci-failures/SKILL.md`

**Interfaces:**
- Consumes: the `--wait` flag and incremental JSON output from Tasks 2-4
- Produces: updated skill instructions for Claude Code

- [ ] **Step 1: Add `--wait` guidance to Step 1**

After the existing "Invocation rules" paragraph in Step 1 of SKILL.md, add:

```markdown
**In-progress runs:** When the run is in-progress (dashboard says "Checks still running"):
- Add `--wait` to poll until all jobs complete with incremental reporting
- `--wait` emits `---`-delimited JSON chunks to stdout: `"type": "partial"` for each batch of new failures, `"type": "final"` for the complete report
- Start diagnosing failures from each `"partial"` report immediately — don't wait for the final report
- Status lines on stderr show progress: `4/7 jobs complete, 2 running, 1 queued`
- Default timeout is 15 minutes (override with `--wait-timeout <seconds>`)

**With `--fix`:** Always pass `--wait` to capture all failures before applying fixes: `pr ci --fix --wait 2>&1`
```

- [ ] **Step 2: Update Step 2 to handle partial reports**

Add a note to the beginning of Step 2:

```markdown
**Incremental mode (with `--wait`):** Process each `---`-delimited JSON chunk as it arrives. For `"type": "partial"` chunks, classify and begin diagnosing the failures immediately — more may arrive. When `"type": "final"` arrives, present the complete classification table and summarize any already-diagnosed failures.
```

- [ ] **Step 3: Commit**

```bash
git add ai/claude/skills/ci-failures/SKILL.md
git commit -m "docs(ci-failures): add --wait guidance to SKILL.md"
```
