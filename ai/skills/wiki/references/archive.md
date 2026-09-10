# Archive

Retire an article that is superseded, wrong, or about something no longer in use.

```bash
wiki archive <slug>           # refuses if live articles still link here
wiki archive <slug> --force   # archive anyway, leaving those links as tombstones
wiki index                    # then rebuild, to drop it from the index
```

Archiving is a move, not a delete. The article keeps its slug, stays readable, and remains
a valid wikilink target — what changes is that it leaves the published set, so the index,
staleness, orphan, and sparse checks stop reporting on something nobody maintains any more.

Deleting instead would lose the record of what was once believed and why it changed, which
is usually the most valuable thing a retired article still holds.

## Confirm first

This moves content the user wrote. Say which article, why it is being retired, and what
links to it. Never pass `--force` on the user's behalf: the refusal it overrides exists so
that live articles are not silently left pointing at retired content.

## When it refuses

```
wiki: old-flow is still linked from current, migration-notes
  remove those links first, or pass --force to keep them as tombstones
```

Two honest answers, and the user picks:

- **Rewrite the links** to the successor article, then archive. Right when the knowledge
  moved somewhere — the readers of those articles should land on what is true now.
- **`--force`**, keeping the links as deliberate tombstones. Right when the reference is
  historical: "this replaced [[old-flow]]" is a sentence that should still resolve.

Forcing records the referrers in `_log.md`, and `wiki lint` reports each surviving link as
`archived-link` from then on — a standing warning, not an error, because the tombstone is a
legitimate end state rather than something to be fixed.

## After archiving

1. `wiki index` — the index is generated from published articles, so it must be rebuilt.
2. `wiki lint` — should be clean, or carry only the `archived-link` warnings you chose.

## What archived articles no longer do

They are targets but not subjects. A link *to* an archived article resolves; the article
itself is no longer scanned for contradictions, broken outbound links, thinness, or
staleness. A retired article's unresolved contradiction is a record of what was once
believed, not a defect anyone should be asked to fix.

They also stop counting as inbound links, so archiving a page does not quietly rescue the
articles it used to reference from being orphans.
