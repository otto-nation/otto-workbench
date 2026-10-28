# Review: acme/widget#38 — feat: retry the flaky fetch

<!-- date: 2026-10-19 -->
<!-- mode: pr -->
<!-- pr: 38 -->
<!-- head_sha: 9f1c2e3d4b5a69788796a5b4c3d2e1f00a1b2c3d -->
<!-- head_ref: feat/retry -->
<!-- base_ref: main -->
<!-- review_type: full -->
<!-- status: partial -->
<!-- generator: review 1.42.0 / otto-workbench 1.47.0 (6c990742) -->
<!-- rendered from review.json — edits here are overwritten; change the record -->

## File Triage

- `src/app.py` — reviewed; adds the retry loop.
- `src/util.py` — reviewed; helper only.
- `src/cli.py` — reviewed; flag parsing.

## Agent Failures

| Agent | Reason | Status |
|-------|--------|--------|
| synthesis | mechanical fallback | fallback |

Run `pr review --recover` to retry failed agents.

## Summary

4 findings across 3 files in 2 groups. Synthesis agent failed — findings below are from individual group reviews.

## Must fix

(none)
- **[M1]** `src/cli.py:4` — `--timeout` is compared before it is validated, so a negative timeout is accepted.

## Should fix

- **[S1]** `src/app.py:2` — The retry loop never sleeps between attempts, so a flapping endpoint is hammered.

## Nit

- **[N1]** `src/util.py:1` — `clamp` duplicates `max(min(...))`; see [S1] for the caller.
- **[N2]** `src/cli.py:1` — Unused import `os`.

## Verdict

Request changes — 1 must-fix, 1 should-fix, 2 nit (mechanically merged, not synthesized).
