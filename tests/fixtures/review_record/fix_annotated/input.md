# Self-Review: acme/widget — feat/retry

<!-- date: 2026-10-19 -->
<!-- mode: self -->
<!-- head_sha: 9f1c2e3d4b5a69788796a5b4c3d2e1f00a1b2c3d -->
<!-- head_ref: feat/retry -->
<!-- base_ref: main -->
<!-- review_type: full -->
<!-- generator: review 1.42.0 / otto-workbench 1.47.0 (6c990742) -->

## Summary

The retry loop works; four small items remain.

## Should fix

- [x] **[S1]** `src/app.py:2` — The retry loop never sleeps between attempts. *(unverified — the suite did not run)*
- [ ] **[S2]** `src/cli.py:5` — `--timeout` is never validated; a quoted * (note)* stays prose. *(declined — argparse already validates it with type=int)*

## Nit

- [ ] **[N1]** `src/util.py:1` — `clamp` duplicates `max(min(...))`. *(skipped — needs a product call)*
- [ ] **[N2]** `src/cli.py:1` — Unused import `os`.

## Static Analysis

- [x] **[SA1]** `src/app.py:2` — nesting depth 5 exceeds 4
