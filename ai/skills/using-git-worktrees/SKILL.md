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
GIT_DIR=$(git rev-parse --absolute-git-dir 2>/dev/null)
GIT_COMMON=$(realpath "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null)
BRANCH=$(git branch --show-current 2>/dev/null)
```

Neither line uses `cd … &&`. A compound `cd` raises an unsuppressible
permission prompt in Claude Code, which loads this skill from the same source
Pi does, and a statement boundary inside `$(…)` counts. `--absolute-git-dir`
canonicalizes on its own; `--git-common-dir` has no such flag, so `realpath`
does it there.

`GIT_DIR != GIT_COMMON` is also true inside a submodule. Rule that out first — if
this prints a path you are in a submodule, not a worktree, so treat it as a
normal repo:

```bash
git rev-parse --show-superproject-working-tree 2>/dev/null
```

A linked worktree is not by itself isolation on this machine. Bare repos here
keep the default branch in a linked worktree of its own — `main/` is a peer of
the feature worktrees, not a primary checkout — so `GIT_DIR != GIT_COMMON` is
true while you stand on `main`. Ask which branch the container itself names:

```bash
DEFAULT=$(git --git-dir="$GIT_COMMON" symbolic-ref --quiet HEAD 2>/dev/null | sed 's|^refs/heads/||')
```

An empty `DEFAULT` is a real answer, not a failure to handle: a repo whose HEAD
is detached has no default branch to stand on, so no branch can equal it and
every comparison below falls through to Step 1 — the safe direction, since it
cuts a worktree rather than assuming isolation.

**If `GIT_DIR != GIT_COMMON`, not a submodule, and `BRANCH` differs from
`DEFAULT`:** you are in a feature worktree. Skip to Step 2. Do not create
another.

Report with branch state:
- On a branch: "Already in isolated workspace at `<path>` on branch `<name>`."
- Detached HEAD: "Already in isolated workspace at `<path>` (detached HEAD,
  externally managed). Branch creation needed at finish time."

**If `BRANCH` equals `DEFAULT`:** you are standing in the default branch's own
worktree, which is the one place these rules forbid writing. Go to Step 1 and
cut a worktree, however much the paths look isolated.

**If `GIT_DIR == GIT_COMMON`:** you are in the primary checkout of a normal
non-bare clone. Go to Step 1.

**If either is empty**, `git rev-parse` failed — you are outside a repository,
or the directory is not readable. Two empty strings compare equal, so this
reads as "normal checkout" when the truth is that nothing could be determined.
Say so and stop rather than creating a worktree from an unknown location.

## Step 1: Create the Worktree with `wt`

Read-only work needs no worktree — searching, reading, and exploring are
explicitly exempt. For anything that writes, a worktree is not optional and not
a question to ask: this machine's rules forbid editing `main`, `master`, or any
shared branch in place.

If the work has an issue, its ID goes in the branch name, so the issue must
exist before the branch does — see the issue-tracker rules for which tracker
this repo uses. Work with no issue behind it uses the `type` form instead
(`javier/feat/add_metrics`); do not stop an unattended run to file one.

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

Any other `wt` failure — no `wt` on PATH, worktrunk unconfigured for this repo,
a sandbox denying the write — stops the work. Report what failed and ask. Both
improvisations are already closed off above, and working in place on the default
branch is not a third option.

`wt` prints the new worktree path but cannot change your shell's directory
unless shell integration is installed. Read the path out of its output and `cd`
there yourself — as a call of its own, with nothing joined to it by `&&` or `;`,
which would make it a compound `cd`. Then confirm you landed, addressing the
worktree by path rather than relying on where you stand:

```bash
git -C <worktree-path> rev-parse --show-toplevel
git -C <worktree-path> branch --show-current
```

In a subagent the bare `cd` does not persist between calls at all, so `git -C`
is not merely tidier there — it is the only form that answers about the right
tree.

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
| Already in a linked worktree, non-default branch | Skip creation (Step 0) |
| In the default branch's worktree (`main/`) | Create anyway — linked is not isolated (Step 0) |
| In a submodule | Treat as a normal repo (Step 0 guard) |
| Either path came back empty | `git rev-parse` failed — report it; do not read it as a normal checkout |
| Normal checkout, work is read-only | No worktree needed |
| Normal checkout, work writes | `wt switch -c` (Step 1) |
| Work has an issue | Its ID goes in the branch name |
| No issue behind the work | Use the `type` form; do not stall to file one |
| `wt switch -c` aborts on the hook | Fix the default branch; never `--no-hooks` |
| `wt` fails any other way | Stop and report — do not improvise a worktree |
| Shell did not change directory | Read the path from `wt` output and `cd` yourself |
| Tests fail at baseline | Report and ask |

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "I'm obviously not in a worktree" | Run Step 0. Harness-created isolation and submodules both fool eyeballing. |
| "`GIT_DIR != GIT_COMMON`, so I'm isolated" | `main/` is a linked worktree here too. Compare the branch against the container's HEAD before believing it. |
| "`git worktree add` is quicker" | Only `wt` applies this machine's naming rules and pre-switch hooks. The git form puts the worktree somewhere the rest of these rules do not describe. |
| "I'll put it in `.worktrees/`" | That is upstream Superpowers' default, not this machine's. Bare repos here place worktrees as peers of `main/`. |
| "The hook abort is a flake — I'll pass `--no-hooks`" | The abort means the default branch is stale or diverged. Branching anyway bases your work on it. |
| "It's a one-line change, I'll just edit here" | Never edit `main` in place, whatever the size. |
| "I'll run the suite in the main worktree, it's the same code" | It is not. Cross-worktree test runs can corrupt the target's git state. |
