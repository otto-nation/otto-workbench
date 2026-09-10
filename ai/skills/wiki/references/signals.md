# Signals

```bash
wiki signals          # tag table, similar pairs, gap clusters, draft ages
wiki signals --json   # the same, for filtering
```

Counted evidence for the judgements `wiki lint` leaves open. Exit is always 0: nothing here
is a defect, and none of it is a conclusion.

`lint` answers *what is broken*. These four answer *what the wiki does not cover, and where
it says the same thing twice* — questions a rule cannot settle but can measure.

| Signal | What it counts | What it is for |
|---|---|---|
| `tags` | Every tag, its count, and the articles carrying it | Spotting near-duplicate tags — see `lint.md` |
| `similar_articles` | Published pairs scoring ≥ 0.55 on shared five-word runs | Finding merge candidates |
| `gap_clusters` | `QUERY_GAP` entries grouped by shared topic words | Seeing what the wiki is repeatedly asked and cannot answer |
| `drafts` | Each draft's age in days and origin | Draft backlog, oldest first |

Report the numbers as given. They are computed; do not re-derive or estimate them.

## Reading gap clusters

A topic queried four times is a different proposition from one queried once. The cluster
count is the signal — but the grouping is single-link on shared words, so it is a starting
point, not a verdict. Every question is carried through in `gaps[]`: read them before
concluding anything, because two questions sharing the word "token" may not be one gap.

A cluster with several entries and no article behind it is the strongest case the wiki gives
for what to compile next.

## What this is not

There is no ranked to-do list here, deliberately. The plugin this skill was ported from had
an `evolve` command that read these same inputs and emitted prioritised suggestions; it was
cut for having no mechanical anchor. This is the anchor, shipped on its own so the ranking
layer can be judged against real numbers from a real wiki rather than designed in advance.

If, after using this on a wiki with a real gap log, the counts turn out to need a ranking
pass on top, that is the point to build one — and it will have data to be right about.
