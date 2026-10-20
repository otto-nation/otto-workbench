# Review: acme/widget#38 — feat: retry the flaky fetch

<!-- date: YYYY-MM-DD -->

## File Triage
- `src/app.py` — reviewed; adds the retry loop.

## Summary
The retry loop is a good direction, but M1 blocks: the timeout is never honoured.

## Must fix
- **[M1]** **`src/cli.py:5`** — `--timeout` is compared as a string.
  > ```python
  > if str(args.timeout) > elapsed:
  > ```

## Should fix
- **[S1]** **`src/app.py:2`** — The retry loop never sleeps between attempts.
  > ```python
  > for attempt in range(3):
  >     call()
  > ```

## Verdict
**Request changes.** M1 must be fixed before merge.
