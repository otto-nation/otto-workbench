# Review: acme/widget#38 — feat: retry the flaky fetch

<!-- date: 2026-10-19 -->
<!-- mode: pr -->
<!-- pr: 38 -->
<!-- head_sha: 9f1c2e3d4b5a69788796a5b4c3d2e1f00a1b2c3d -->
<!-- head_ref: feat/retry -->
<!-- base_ref: main -->
<!-- review_type: full -->
<!-- generator: review 1.42.0 / otto-workbench 1.47.0 (6c990742) -->

## File Triage
- `src/app.py` — reviewed; adds the retry loop.

## Summary
The retry loop is a good direction, but M1 blocks: the timeout is never honoured.

<!-- verification-drops -->
> **Evidence verification removed 1 finding:**
> - Must fix — `src/cli.py`: evidence mismatch at char 3

## Must fix
## Should fix
- **[S1]** **`src/app.py:2`** — The retry loop never sleeps between attempts.

## Verdict
Needs discussion — 1 should-fix after evidence verification removed 1 finding.

