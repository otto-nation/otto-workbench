## File Triage
- `src/app.py` — reviewed; overlaps group 1.
- `src/cli.py` — reviewed; flag parsing.

## Must fix
- **[M1]** **`src/cli.py:4`** — `--timeout` is compared before it is validated, so a negative timeout is accepted.
  > ```python
  > if args.timeout > elapsed:
  > ```

## Should fix
- **[S1]** **`src/app.py:2`** — The retry loop never sleeps between attempts, so a flapping endpoint is hammered.

## Nit
- **[N1]** **`src/cli.py:1`** — Unused import `os`.
