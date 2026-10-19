# Review record golden fixtures (PR 0 parity)

Captured from otto-workbench 765f6a9e, before `review.json` existed, by a
scratch script that drove today's writers directly:

| Case | Produced by |
|---|---|
| synthesis_pr, self_incremental_fixed | handwritten in the shape of real synthesis / incremental self-reviews (fictional content) |
| merged | `review.merge.merge_reviews(groups/group-1.md, groups/group-2.md)` |
| mechanical_fallback | `review.outcome._write_mechanical_fallback` + `review.state._inject_failures_and_status` |
| clean | `review.outcome._write_clean_review` |
| unchanged | `review.outcome.write_unchanged_review` with `synthesis_pr` as the prior review |
| evidence_dropped | `review.outcome._post_process_review` over `raw.md` against `tree/` |
| fix_annotated | `review.fix._apply_outcomes` + `_apply_static_outcomes` over `before.md` |
| rebuild | `review.rebuild.rebuild` over `groups/` |

`input.md` is what today's code wrote. `expected.md`, where present, is the same
document with its sections in spec §2 order (deviations D2/D4 in the plan).
`meta.json` is the `ReviewMeta` whose rendered header equals `input.md`'s.
Parity is `canonical(render(...)) == canonical(expected.md or input.md)` — see
`tests/review_record_support.py`.

The tree under `tree/src/` is stored as `*.py.txt` (`app.py.txt`, `cli.py.txt`,
`util.py.txt`) so `bin/local/validate-test-layout` does not treat those files as
Python test modules. The capture script copies the tree and strips the `.txt`
suffix so the scratch tree it drives has real `.py` files.
