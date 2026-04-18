---
name: anatomy
description: "Generate or refresh a project file index (.claude/anatomy.md) with per-file descriptions and token estimates. Helps Claude decide what to read before exploring."
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

`.claude/anatomy.md` in the project root (gitignored, not committed).
