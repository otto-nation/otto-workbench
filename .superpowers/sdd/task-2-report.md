## Task 2 Report: Add cmd_rebase to pr CLI

**Status:** DONE

---

### Commits

- `4b74a6d` feat(pr): add rebase subcommand with conflict detection and force-push

---

### Changes Made

**`ai/claude/bin/pr`:**
- Updated module docstring to include `pr rebase` in subcommands and usage
- Added `_render_rebase_section(r: pr_state.RebaseSummary) -> list[str]` after `_render_triage_section`
- Added `_detect_rebase_in_progress(cwd)`, `_detect_conflicts(cwd)`, `_rebase_head_info(cwd)`, `_remaining_rebase_commits(cwd)` helpers after `cmd_gc`
- Added `cmd_rebase(args, extra, ctx)` with `--abort`, `--push`, and default rebase flow
- Added `_rebase_success(args, ctx, cwd)` helper for clean rebase completion
- Added `rebase` argparse subparser with `--fix`, `--push`, `--abort` flags after `gc` subparser
- Added `"rebase": cmd_rebase` to the dispatch dict
- Added rebase section rendering in `cmd_status` after triage section

**`tests/pr_cli_test.py`:**
- Added 4 tests for `_render_rebase_section`: `not_run`, `with_data`, `no_conflicts`, `not_pushed`

---

### TDD Progression

1. Wrote 4 failing tests for `_render_rebase_section` — confirmed `AttributeError: module 'pr_cli' has no attribute '_render_rebase_section'`
2. Implemented `_render_rebase_section` — all 4 render tests passed
3. Implemented remaining helpers, `cmd_rebase`, `_rebase_success`, argparse wiring, dispatch, `cmd_status` update, and docstring
4. Ran full test suite — all pass

---

### Test Results

```
pytest tests/pr_cli_test.py tests/pr_state_test.py -v
```

**52 passed, 0 failed**

- `pr_cli_test.py`: 25 tests (including 4 new `render_rebase_section` tests)
- `pr_state_test.py`: 27 tests

---

### Concerns

None.
