# Review record golden fixtures (PR 0 parity)

The record cases are `record.json` (and `evidence_dropped/raw.json`,
`fix_annotated/before.json`), converted at schema 2 from the PR-0 markdown
captures by Task 4's script; the markdown is gone.

Parity is `canonical(render(record.json)) == canonical(expected.md)`.

`merged/expected.md` is `merge_records` over `groups/group-N.json`, rendered
with sids and evidence (Task 8).

The tree under `tree/src/` is stored as `*.py.txt` (`app.py.txt`, `cli.py.txt`,
`util.py.txt`) so `bin/local/validate-test-layout` does not treat those files as
Python test modules. The capture script copies the tree and strips the `.txt`
suffix so the scratch tree it drives has real `.py` files.
