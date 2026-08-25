---
name: anatomy
description: "Generate or refresh a project file index (.claude/anatomy.md) with per-file descriptions and token estimates. Helps Claude decide what to read before exploring. TRIGGER when: user wants an overview of codebase structure, before exploring an unfamiliar project, or after significant file changes. SKIP: user asks about a specific known file — read it directly."
source: otto-workbench/ai/claude/skills/anatomy/SKILL.md
invocation: "/anatomy"
trigger: "Run to refresh the project file index before exploring an unfamiliar codebase, or after significant file changes."
skip: "Do not use when the user asks about a specific file they already know — just read it directly."
output: ".claude/anatomy.md"
lifecycle_cadence: "on HEAD change"
lifecycle_scope: per-project
---

# Anatomy — Project File Index

Generates `.claude/anatomy.md` — a compact catalog of the project's tracked files
with line counts, token estimates, and descriptions extracted from source comments.

## When to use

- Before exploring an unfamiliar codebase: read `.claude/anatomy.md` to understand
  the file layout and decide which files to open
- To refresh the index after significant changes: run `/anatomy` to regenerate

## How it works

The generator scans `git ls-files`, extracts the first meaningful comment from each
file (lines 1-15), estimates tokens as `lines × 4`, and writes a markdown table
grouped by directory. It skips binary files, lock files, and generated code.

## Regeneration

The index auto-regenerates via the Stop hook when the git HEAD changes. To force
a manual refresh:

```bash
bash ~/.claude/skills/anatomy/generate-anatomy.sh
```

The generator is idempotent — repeated runs with the same git HEAD are instant no-ops.

## Output location

`.claude/anatomy.md` in the project root (gitignored, not committed) — the root
`git rev-parse --show-toplevel` names from wherever the generator was run, so the index
always describes the tree it is written into.

A bare-repo container has no working tree, and so gets no index at all. Every path an
index lists exists in a worktree rather than beside the bare `.git`, nothing there is
covered by a `.gitignore` rule, and no session rooted at a container reads one anyway:
the `claude` shell wrapper launches such a session in a worktree instead.
