## File Triage
- `src/app.py` — reviewed; adds the retry loop.
- `src/util.py` — reviewed; helper only.
- `src/cli.py` — reviewed; flag parsing.

## Must fix
(none)
- **[M1]** **`src/cli.py:4`** — `--timeout` is compared before it is validated, so a negative timeout is accepted.
  > ```python
  > if args.timeout > elapsed:
  > ```

## Should fix
- **[S1]** **`src/app.py:2`** — The retry loop never sleeps between attempts, so a flapping endpoint is hammered.
  > ```python
  > for attempt in range(3):
  >     call()
  > ```
## Nit
- **[N1]** **`src/util.py:1`** — `clamp` duplicates `max(min(...))`; see [S1] for the caller.
- **[N2]** **`src/cli.py:1`** — Unused import `os`.
