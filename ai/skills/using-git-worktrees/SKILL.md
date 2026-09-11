---
name: using-git-worktrees
description: "Ensure work happens in an isolated worktree before implementation begins. TRIGGER when: starting feature work that needs isolation, or before executing an implementation plan. SKIP: read-only exploration, which needs no worktree."
source: otto-workbench/ai/skills/using-git-worktrees/SKILL.md
invocation: "/using-git-worktrees"
trigger: "Use when starting feature work that needs isolation, before executing an implementation plan, or when the user asks to set up a worktree."
skip: "Do not use for read-only work — searching, reading, and exploring are exempt from this machine's worktree rules."
---

<!-- Overrides superpowers:using-git-worktrees, which cuts `git worktree add`
     into .worktrees/ at the project root. This machine uses `wt`, and its bare
     repos place worktrees as peers of main/ rather than inside the repo, so the
     upstream skill would create a worktree in a location none of the rest of
     these rules describe.

     This file wins by name collision: Pi ranks ~/.agents/skills (scope user,
     origin top-level) ahead of a package resource, so the upstream copy is
     dropped even though the Superpowers extension re-adds its skills directory
     through resources_discover.

     Written against superpowers v6.3.0. When bumping the pin, re-read the
     upstream skill: its callers (executing-plans, subagent-driven-development,
     writing-plans) reference it by name, and a changed contract lands here. -->

# Using Git Worktrees

Ensure work happens in an isolated worktree. On this machine that means the `wt`
CLI, which applies the naming rules and pre-switch hooks the rest of the
workbench assumes.

**Announce at start:** "I'm using the using-git-worktrees skill to set up an
isolated workspace."

## Step 0: Detect Existing Isolation

Before creating anything, check whether you are already isolated.

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" 2>/dev/null && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P)
BRANCH=$(git branch --show-current)
```

`GIT_DIR != GIT_COMMON` is also true inside a submodule. Rule that out first — if
this prints a path you are in a submodule, not a worktree, so treat it as a
normal repo:

```bash
git rev-parse --show-superproject-working-tree 2>/dev/null
```

**If `GIT_DIR != GIT_COMMON` and not a submodule:** you are already in a linked
worktree. Skip to Step 2. Do not create another.

Report with branch state:
- On a branch: "Already in isolated workspace at `<path>` on branch `<name>`."
- Detached HEAD: "Already in isolated workspace at `<path>` (detached HEAD,
  externally managed). Branch creation needed at finish time."

**If `GIT_DIR == GIT_COMMON`:** you are in a normal checkout, which on this
machine is usually the default branch's worktree.

**If either is empty**, `git rev-parse` failed — you are outside a repository,
or the directory is not readable. Two empty strings compare equal, so this
reads as "normal checkout" when the truth is that nothing could be determined.
Say so and stop rather than creating a worktree from an unknown location.

## Step 1: Create the Worktree with `wt`

Read-only work needs no worktree — searching, reading, and exploring are
explicitly exempt. For anything that writes, a worktree is not optional and not
a question to ask: this machine's rules forbid editing `main`, `master`, or any
shared branch in place.

An issue must exist before the branch does, because the branch name embeds its
ID. If there is no issue yet, file one first — see the issue-tracker rules for
which tracker this repo uses.

```bash
wt switch -c <username>/<ISSUE-or-type>/<description_in_snake_case>
```

Branch naming, base branch, and worktree placement are all `wt`'s to decide. Do
not pass a path, and do not fall back to `git worktree add` — the two produce
different layouts, and the git form leaves state `wt` cannot see.

`wt switch -c` brings the default branch up to date before cutting the new one,
through the `fetch-default` pre-switch hook. **If it aborts, that is the
guarantee working**, not an error to route around: the default branch has
diverged from origin or will not fast-forward. Fix the default branch and
re-run. Never re-run with `--no-hooks` to get past it.

`wt` prints the new worktree path but cannot change your shell's directory
unless shell integration is installed. Read the path out of its output and `cd`
there yourself, then confirm you landed:

```bash
git rev-parse --show-toplevel
git branch --show-current
```

## Step 2: Project Setup

Auto-detect and run the appropriate setup:

```bash
if [ -f package.json ]; then npm install; fi
if [ -f Cargo.toml ]; then cargo build; fi
if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
if [ -f pyproject.toml ]; then poetry install; fi
if [ -f go.mod ]; then go mod download; fi
```

## Step 3: Verify a Clean Baseline

Run the project's tests so the workspace starts green. Use the project's own
command — `task test`, `npm test`, `cargo test`, `pytest`, `go test ./...`.

Run them **in this worktree**, never in another. A test harness that creates
temporary git repos can corrupt the branch and commit state of whatever tree it
runs in.

**If tests fail:** report the failures and ask whether to proceed or
investigate. A dirty baseline makes every later failure ambiguous.

**If tests pass:** report ready.

### Report

```
Worktree ready at <full-path> on branch <name>
Tests passing (<N> tests, 0 failures)
Ready to implement <feature-name>
```

## Quick Reference

| Situation | Action |
|-----------|--------|
| Already in a linked worktree | Skip creation (Step 0) |
| In a submodule | Treat as a normal repo (Step 0 guard) |
| Either path came back empty | `git rev-parse` failed — report it; do not read it as a normal checkout |
| Normal checkout, work is read-only | No worktree needed |
| Normal checkout, work writes | `wt switch -c` (Step 1) |
| No issue yet | File the issue first — the branch name needs its ID |
| `wt switch -c` aborts on the hook | Fix the default branch; never `--no-hooks` |
| Shell did not change directory | Read the path from `wt` output and `cd` yourself |
| Tests fail at baseline | Report and ask |

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "I'm obviously not in a worktree" | Run Step 0. Harness-created isolation and submodules both fool eyeballing. |
| "`git worktree add` is quicker" | Only `wt` applies this machine's naming rules and pre-switch hooks. The git form puts the worktree somewhere the rest of these rules do not describe. |
| "I'll put it in `.worktrees/`" | That is upstream Superpowers' default, not this machine's. Bare repos here place worktrees as peers of `main/`. |
| "The hook abort is a flake — I'll pass `--no-hooks`" | The abort means the default branch is stale or diverged. Branching anyway bases your work on it. |
| "It's a one-line change, I'll just edit here" | Never edit `main` in place, whatever the size. |
| "I'll run the suite in the main worktree, it's the same code" | It is not. Cross-worktree test runs can corrupt the target's git state. |
