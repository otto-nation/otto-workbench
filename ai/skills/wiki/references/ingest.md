# Ingest

Bring a source into `raw/` so it can be compiled. Ingest never writes articles — that is
compile's job.

```
/wiki ingest <source>
/wiki ingest --research "topic"
/wiki ingest --conversation ["topic name"]
/wiki ingest --no-compile <source>
```

| Input | Type |
|---|---|
| URL on a code-hosting domain (github.com, gitlab.com, ...) | repo |
| Any other `http://` or `https://` URL | web |
| Path ending `.pdf` | pdf |
| Path to a directory | directory |
| `--research "topic"` | research |
| `--conversation` | conversation |
| Any other path | file |

## Validate first

Before spending tokens: is the source reachable? Is it already ingested — check
`wiki sources` for the path and hash, and skip unless `--force`. Is it a manageable size,
or should it be chunked? Is the type one of the above?

Report and stop if not. Bad input should cost nothing.

## Every source lands the same way

For a source that is already a local file, stage it with the CLI rather than copying it by
hand:

```bash
wiki ingest --stage <path> --type <type> --title "{title}"
```

That copies it into `raw/`, names it `{type}-{slug}` with a slug it derives, adds
frontmatter, logs the ingest, and prints the manifest row — hash included, computed from
the bytes. Staging the same filename twice keeps both.

For a source you had to fetch or assemble — a web page, a repo analysis, research — write
the extracted markdown to a temporary file first, then stage that. The handlers below say
what to extract.

The frontmatter, whichever way it lands:

```markdown
---
source_type: {web|repo|pdf|file|directory|research|conversation}
title: "{title}"
ingest_date: {YYYY-MM-DD}
---

{content}
```

Type-specific keys are added below. **Do not put a `content_hash` in frontmatter.** Hashes
come from `wiki sources`, which computes them from file bytes; a hash written by hand is a
guess, and a wrong one silently defeats incremental compilation.

After staging, run `wiki sources --new` to confirm it registers, then compile unless
`--no-compile`.

## Handlers

**Web** — fetch the URL, then extract the substantive content: the article or documentation
itself. Strip navigation, footers, ads, cookie banners, sidebars. This needs judgement, not
a regex. Add `url:` and `fetch_date:`.

**Repo** — shallow clone to a temp directory. Read README, contributing guide, package
manifests, and any architecture docs first, then look at route definitions, schemas,
migrations, config, entry points, and error types. Write an analysis covering what the repo
does, its stack, its major modules and how they connect, its domain objects and data flows,
and any notable design decisions. Add `url:`, `name:`, `tech_stack:`. Clean up the clone.

Concepts and architecture, not an inventory: a 500-file repo is a 500-2000 word analysis,
never a file listing.

**PDF** — extract title, authors, date, abstract, key claims and findings, data points,
described figures and tables, and references. Read in sections for a long document,
prioritising abstract, introduction, conclusions, and figures. Add `authors:`, `date:`,
`pages:`. Note anything that could not be captured: `Figure 3: [complex diagram, see
original page 12]`.

Not every harness can read PDFs directly. Where the read tool cannot, convert first
(`pdftotext`) rather than guessing at the contents.

**File** — read it. Well-structured markdown keeps its structure; add frontmatter if
missing. Add `original_path:`.

**Directory** — find the supported files (`.md`, `.txt`, `.rst`, `.pdf`), report the count,
and confirm before ingesting a large batch. Each file becomes its own source.

**Research** — search for the topic, evaluate which results are worth reading, fetch the
few most promising, and write one consolidated source noting where each claim came from.
Add `topic:` and `urls:`.

**Conversation** — capture the current session: what was being worked on, what was
discovered, what was decided and why, and anything that would save the next person the same
investigation. Skip the false starts unless the dead end is itself the finding.

For a debugging session, use the case-study shape — Symptoms, Investigation, Root Cause,
Fix, Prevention — and keep the identifiers: ticket numbers, error messages, the exact
commands that resolved it. Someone hitting the same symptoms should be able to follow the
path, and the identifiers are how they confirm it is the same problem.

## Log it

`wiki ingest --stage` writes the log line itself. Add one by hand only for a source it did
not stage:

```
[{DATE}] INGEST: {source} → raw/{filename}
  Type: {type}
```
