# CI Wait Mode and Log Fallback Improvement

Two changes to `pr ci` / `ci-check`: a `--wait` flag for in-progress CI runs with
incremental failure reporting, and a fix to log fallback so generic annotations
always trigger log fetching.

## Files

- `ai/claude/bin/ci-check` — poll loop, `--wait` flag, incremental output
- `ai/claude/lib/ci_failures.py` — dashboard status line
- `ai/claude/skills/ci-failures/SKILL.md` — skill guidance for `--wait` output

## 1. `--wait` flag and poll loop

### New CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--wait` | off | Poll until all jobs complete |
| `--wait-timeout` | 900 (15 min) | Max wait time in seconds |
| `--wait-interval` | 30 | Poll interval in seconds |

### Poll loop

New function `_run_ci_wait()` is called instead of `_run_ci()` when `--wait` is active.
`_run_ci()` remains unchanged for the non-wait path. Steps:

1. Fetch run data (same as today)
2. Identify newly-failed jobs (by `databaseId`) not yet reported
3. For new failures: fetch annotations/logs, classify, emit partial JSON to stdout
4. Emit status line to stderr: `⏳ 4/7 jobs complete, 2 running, 1 queued`
5. If all complete or timeout: emit final JSON report, break
6. Sleep `interval`, repeat

Job-level tracking: a job `databaseId`, once its failures are emitted, is never
re-processed. The poll re-fetches run data each cycle (job status changes) but only
runs annotation/log extraction for newly-failed jobs.

State persistence: `sync_ci_domain()` is called once at the end with the complete
run state. Partial reports do not touch the state file.

### Job state counting

New helper `_count_job_states(merged)` iterates all jobs and counts by status:
- `completed` with conclusion in `_FAILURE_CONCLUSIONS` or `success` or `neutral`
- `in_progress` — actively running
- `queued` / `waiting` / `pending` — not started yet

## 2. Incremental JSON output format

Each incremental report is a complete JSON object on stdout, separated by a `---`
line on its own. The skill splits on `---` and parses each chunk independently.

### Schema

Partial reports:
```json
{"type": "partial", "completed": 3, "total": 7,
 "failures": [...], "progression": {...}}
```

Final report:
```json
{"type": "final", "completed": 7, "total": 7,
 "repo": "...", "branch": "...", "run_id": "...",
 "failures": [...], "progression": {...},
 "resolved_since_prior": [...], "behind_main": 0}
```

- `"partial"` — more jobs pending; `failures` contains only newly-failed items since last emission
- `"final"` — all jobs done or timeout; `failures` contains ALL failures, plus full report fields

### Backward compatibility

Without `--wait`, output is unchanged — a single JSON report with no `type` field
and no `---` delimiters.

## 3. Log-fetching improvement

### Problem

`_annotations_uninformative()` requires both a missing source path AND a generic
message to trigger log fallback. An annotation with path `.github/workflows/ci.yml`
and message "Process completed with exit code 1" is considered "informative" because
it has a path with an extension — so logs are never fetched.

### Fix

Flip to message-first logic: if the message matches `_GENERIC_MESSAGES`, the
annotation is uninformative regardless of path.

```python
for a in non_notices:
    msg = (a.get("message", "") or "").lower()
    if any(g in msg for g in _GENERIC_MESSAGES):
        continue  # generic = uninformative regardless of path
    path = a.get("path", "")
    has_source_path = bool(path) and "." in path.rsplit("/", 1)[-1]
    if has_source_path:
        return False  # real path + non-generic message = informative
return True
```

### Expanded generic messages

```python
_GENERIC_MESSAGES = (
    "process completed with exit code",
    "a]process completed with exit code",
    "check failure on line",
    "failed with exit code",
    "exited with code",
    "returned a non-zero code",
)
```

## 4. SKILL.md changes

### `--wait` guidance

Step 1 (Fetch status):
- When using `--fix`, always pass `--wait` to capture all failures before fixing
- If output contains `"type": "partial"`, start diagnosing immediately — more may come
- Read each `---`-delimited chunk as a separate JSON report
- When `"type": "final"` arrives, do a final summary pass

### Dashboard status

When `--wait` is active, dashboard header includes run status:
- `## CI Run #11818 (129487d) -- in progress`
- `## CI Run #11818 (129487d) -- complete`

Without `--wait`, header is unchanged.
