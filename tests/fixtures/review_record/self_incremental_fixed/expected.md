# Self-Review: acme/platform — isaac/ENG-1/exactly_once

<!-- date: 2026-10-19 -->
<!-- mode: self -->
<!-- pr: 4560 -->
<!-- head_sha: c637bb6512253dc83a080d1d7fc01759e7523592 -->
<!-- head_ref: isaac/ENG-1/exactly_once -->
<!-- base_ref: main -->
<!-- review_type: incremental -->
<!-- prior_sha: d54f88931bcca25ad039ebb2428cf4310a50c7ec -->
<!-- prior_date: 2026-10-17 -->
<!-- delta_files: 3 -->
<!-- skipped_groups: 0/6 -->
<!-- status: completed -->
<!-- generator: review 1.42.0 / otto-workbench 1.47.0 (b25a5b64) -->
<!-- rendered from review.json — edits here are overwritten; change the record -->

## Summary

The branch claims each slot in the same transaction as the ledger legs; the design is sound.

## Should fix

- [ ] **[S1]** `workflows/schedule.go:261` — The reuse policy is added without a version gate. *(skipped — a person should verify the replay comparison.)*

## Nit

- [x] **[N1]** `ledger/lock_order.go:18` — `compareUUIDs` calls `orderUUIDs` just to reuse byte order.
- [ ] **[N2]** `service/lock_test.go:30` — The test ignores `low`. *(declined — the test passes {high, mid, low}, so the locker must take low first.)*
- [x] **[N3]** `service/reconcile.go:48` — The claim transaction is not checked. *(unverified — the suite did not run)*

## Static Analysis

All checks passed.
