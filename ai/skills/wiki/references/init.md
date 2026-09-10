# Init

Create a knowledge base.

```
/wiki init [path]
```

Default path is `wiki/` in the current directory, or whatever `wiki.dir` is set to. Run
`wiki path` first — if it resolves, one already exists; say where and stop.

To keep the base somewhere else, set the name once rather than passing a path every time:

```bash
otto-workbench config set wiki.dir docs/knowledge --project
```

## 1. Ask two questions

1. **What is this wiki about?** The domain.
2. **Who will use it?** The audience.

If the user skips, use generic defaults and move on. `SCHEMA.md` is editable.

## 2. Create it

```bash
wiki init --domain "{domain}" --audience "{audience}"
```

That writes the whole layout — `SCHEMA.md` from the template with the domain and audience
filled in, the empty `_index.md`, the `_sources.md` header, the first `_log.md` entry, and
`raw/`, `articles/`, `drafts/`, `archive/`, `meta/`. Do not write these by hand: the
manifest header in particular has a shape `wiki` parses.

It refuses if a knowledge base is already there, and completes one left half-written by an
interrupted run rather than starting over.

## 3. Check it

```bash
wiki path      # should print the new directory
wiki status    # should report zero articles and zero sources
wiki lint      # should be clean
```

If `wiki path` does not resolve, one of `SCHEMA.md`, `articles/`, or `raw/` is missing or
misplaced — all three identify the base. Fix that before going further; every other
operation depends on it.

## 4. Report

```
Knowledge base created at {path}/
  Domain: {domain}

  /wiki ingest <source>   add a source
  /wiki compile           compile sources into articles
  /wiki "<question>"      query it
```

## Making sessions aware of it

Nothing further is needed. The workbench rule in `ai/guidelines/rules/artifacts.md` tells
every session in every harness to check for a knowledge base and read the index when one
exists — so a wiki created here is visible to Claude Code and Pi alike, without editing
`CLAUDE.md`, `AGENTS.md`, or any per-project file.
