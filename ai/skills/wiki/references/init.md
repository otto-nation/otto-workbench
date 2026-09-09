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

## 2. Create the structure

```
{path}/
  SCHEMA.md     # from assets/SCHEMA.template.md, filled in
  _index.md     # empty until the first compile
  _log.md       # starts with the init entry
  _sources.md   # empty manifest
  raw/
  articles/
  drafts/
  archive/
  meta/glossary.md
```

`SCHEMA.md` is what makes this directory a knowledge base — `wiki path` looks for exactly
that file. Copy `assets/SCHEMA.template.md` and substitute the domain and audience;
everything else in it is a working default.

`_sources.md` starts as the manifest header:

```markdown
# Source Manifest

| Source | Hash | Type | Ingested | Articles |
| --- | --- | --- | --- | --- |
```

The first two columns are read by `wiki`; hashes come from `wiki sources`, never by hand.

`_log.md` starts with:

```markdown
# Activity Log

[{DATE}] INIT: {domain}
```

## 3. Check it

```bash
wiki path      # should print the new directory
wiki status    # should report zero articles and zero sources
```

If `wiki path` does not resolve, `SCHEMA.md` is missing or misplaced. Fix that before
going further — every other operation depends on it.

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
