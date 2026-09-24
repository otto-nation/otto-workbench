# Init

Create a knowledge base.

```
/wiki init [path]
```

Run `wiki path` first — if it resolves, one already exists; say where and stop.

## 1. Ask where it goes

Two placements, and `wiki init` refuses rather than guessing between them. They differ in
who can read the result, so this is the user's call, not yours:

| | Flag | Where it lands | Who sees it |
|---|---|---|---|
| Private to this machine | `--vault` | The machine's vault, one folder per repo | Only this machine |
| Committed with the repo | `--in-repo` | `wiki/` in the repo, or `wiki.dir` | Everyone who clones |
| A specific directory | `--wiki DIR` | Exactly there | Depends where you point it |

A repo that has already set `wiki.dir` has answered: its base is in-tree, and no flag is
needed.

The vault is found through `wiki.root`, not by searching, so it reads the same from every
worktree of a repo and survives `wt remove`. `wiki init --vault` sets that key the first
time it is used, defaulting to `~/.local/share/workbench/wiki`. A repo with no origin
remote gets no vault folder — it has no name to file under, and two local repos sharing a
name would share one base.

### Browsing the vault from the repo

A vault base is outside every worktree, so `cd wiki` and an editor's file tree no longer
reach it. An opt-in symlink restores that:

```bash
otto-workbench config set wiki.link true
wiki link
```

The link goes beside a bare repo's worktrees, not inside one — `wt remove` cannot strand
it there, it needs no `.gitignore` entry, and it cannot be committed (a committed symlink
stores an absolute machine-specific path). A plain clone has nowhere to put one and is
told so.

It is browsing only. Nothing resolves through it, so a stale or missing link costs
nothing. `wiki link` with the key off removes a link the workbench made, and never touches
one it did not.

### Backups

A knowledge base sits outside every repo, so no `git push` covers it, and it is the one
tree here that nothing can regenerate.

```bash
wiki backup                      # snapshot it
wiki backup --list               # what snapshots exist
wiki backup --restore latest     # extract one *beside* the base, never over it
```

Snapshots go under the state root, ten kept per base. A restore never overwrites the live
base — it extracts alongside and prints where, and moving it into place is the user's call.
`wiki status` says when a base has gone 30 days without one.

**This is same-disk.** It survives a bad compile, a stray `rm`, and an editor that ate a
file. It does not survive losing the drive. For that, point a real backup tool at the
vault — it is an ordinary directory:

```bash
restic backup ~/.local/share/workbench/wiki
```

To keep an in-tree base somewhere other than `wiki/`, set the name once rather than
passing a path every time:

```bash
otto-workbench config set wiki.dir docs/knowledge --project
```

Set `wiki.dir` per project, but never `wiki.root` — it is an absolute path on one machine,
and a repo's `.workbench.yml` is read by everyone who clones it.

## 2. Ask two questions

1. **What is this wiki about?** The domain.
2. **Who will use it?** The audience.

If the user skips, use generic defaults and move on. `SCHEMA.md` is editable.

## 3. Create it

```bash
wiki init --vault --domain "{domain}" --audience "{audience}"
```

or `--in-repo` in place of `--vault`, per the choice above.

That writes the whole layout — `SCHEMA.md` from the template with the domain and audience
filled in, the empty `_index.md`, the `_sources.md` header, the first `_log.md` entry, and
`raw/`, `articles/`, `drafts/`, `archive/`, `meta/`. Do not write these by hand: the
manifest header in particular has a shape `wiki` parses.

It refuses if a knowledge base is already there, and completes one left half-written by an
interrupted run rather than starting over.

## 4. Check it

```bash
wiki path      # should print the new directory
wiki status    # should report zero articles and zero sources
wiki lint      # should be clean
```

If `wiki path` does not resolve, one of `SCHEMA.md`, `articles/`, or `raw/` is missing or
misplaced — all three identify the base. Fix that before going further; every other
operation depends on it.

## 5. Report

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
