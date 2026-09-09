# Query

Answer a question from compiled articles. Anything that is not a known subcommand is a
question.

## 1. Find the articles

Read `_index.md` and pick the articles whose summaries bear on the question. Where the wiki
has domain subdirectories with their own indexes, read the relevant one next.

This is a semantic scan, not a keyword search, which is why it beats grep for questions
like "how do these two relate?" where no single term appears in either article. The index
exists to be read this way — one line per article, kept scannable.

Start with the 3-5 most directly relevant and stop around 15. Prefer high-confidence
articles over low, recent over stale.

## 2. Read

Read the selected articles in full. Follow a `[[wikilink]]` when the linked article looks
relevant — one hop, not transitively. Note contradictions between articles, and note where
the articles touch the question without answering it.

## 3. Answer

```
{The direct answer, first}

{Supporting detail and context}

**Sources:** [[article-a]], [[article-b]]
**Confidence:** {high|medium|low|no coverage}
```

- Cite with `[[article-name]]` so the reader can check you.
- Separate wiki knowledge from your own: "the wiki does not cover X directly, but
  generally..." Never present training knowledge as something the wiki says.
- Say when the wiki is insufficient. That is a useful answer; an invented one is not.
- Where articles disagree, give both and name the contradiction. Do not quietly pick.

Confidence: **high** — several articles address it directly, no contradictions, current.
**medium** — one or two, or tangential, or possibly stale. **low** — nothing direct, the
answer leans on extrapolation. **no coverage** — the wiki has nothing.

## 4. Log the gap

At medium or below, append to `_log.md`:

```
[{DATE}] QUERY_GAP: "{question}"
  Confidence: {level}
  Consulted: [[article-a]], [[article-b]]
  Missing: {what would have answered it}
```

These accumulate into the record of what the wiki should cover next. `wiki lint` reports
them as unprocessed until a compile acts on them — which is the mechanism by which being
used makes the wiki better.

## 5. Offer to crystallize

If the answer connected two or more articles into something no single article states — a
comparison, a pattern across services, a causal chain assembled from parts — offer to keep
it:

```
This answer connected [[article-a]] and [[article-b]] in a way no existing article
captures. Create a draft? [y/n]
```

On yes, write to `drafts/` with `origin: crystallized`, `crystallized_from: [...]`,
`crystallized_query: "..."`, and `confidence: medium`, then log
`[{DATE}] CRYSTALLIZED: "{title}"`.

Be conservative. The bar is whether someone searching this topic would want it as its own
article. Offered on every query, it is noise.
