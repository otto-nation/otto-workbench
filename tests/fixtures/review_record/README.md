# Review record golden fixtures (PR 0 parity)

Captured from otto-workbench 765f6a9e, before `review.json` existed, by a
scratch script that drove today's writers directly:

| Case | Produced by |
|---|---|
| synthesis_pr, self_incremental_fixed | handwritten in the shape of real synthesis / incremental self-reviews (fictional content) |
| merged | `review.merge.merge_records` over `groups/group-1.json`, `groups/group-2.json` |
| mechanical_fallback | `review.outcome._write_mechanical_fallback` + `review.state._inject_failures_and_status` |
| clean | `review.outcome._write_clean_review` |
| unchanged | `review.outcome.write_unchanged_review` with `synthesis_pr` as the prior review |
| evidence_dropped | `review.outcome._post_process_review` over `raw.md` against `tree/` |
| fix_annotated | captured from `review.fix._apply_outcomes` + `_apply_static_outcomes` over `before.md` (those helpers are gone; the same cases now go through `review.fix.apply_outcomes` / `apply_static_outcomes`) |
| rebuild | `review.rebuild.rebuild` over `groups/` |

`groups/group-{1,2}.json` replaced the markdown group files. The merged `input.md`
regenerated from them drops the empty `Must fix` `(none)` line the markdown
ingest used to keep; the group artifact has no field for prose under a
severity heading (X13).

Each record case also has `record.json` (and `evidence_dropped/raw.json`, `fix_annotated/before.json`): the same review, written at schema 2 by `save_record` over today's ingest of the markdown beside it. Until the markdown is deleted, `review_render_test.py::test_every_fixture_record_is_its_markdown_ingested` holds the two equal.

`input.md` is what today's code wrote. `expected.md` is the same document as
the record now renders it: schema 2, locations spelled from the fields (X1),
sections in spec §2 order (D2/D4). Every parity case has an `expected.md`.
`meta.json` is the `ReviewMeta` whose rendered header equals `input.md`'s.
Parity is `canonical(render(...)) == canonical(expected.md)` — see
`tests/review_record_support.py`.

The tree under `tree/src/` is stored as `*.py.txt` (`app.py.txt`, `cli.py.txt`,
`util.py.txt`) so `bin/local/validate-test-layout` does not treat those files as
Python test modules. The capture script copies the tree and strips the `.txt`
suffix so the scratch tree it drives has real `.py` files.
