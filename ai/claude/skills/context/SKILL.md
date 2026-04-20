---
name: context
description: "On-demand context.md refresh. Reads recent sessions and memory to identify architectural facts that are missing or stale, then proposes specific additions to .claude/context.md."
---

# Context — Project Context Refresh

Refreshes `.claude/context.md` with architectural facts discovered in recent sessions.
Lighter than `/dream` — focuses only on context.md, not memory consolidation.

Run manually with `/context` after adding a new service, discovering a wrong-API assumption,
or when `<!-- last-reviewed -->` is more than 14 days old and active work is ongoing.

---

## Steps

### 1. Read current state

```bash
# Find the current project's context.md
cat .claude/context.md

# Find the project's memory files (if any)
ls ~/.claude/projects/$(basename $(git rev-parse --show-toplevel 2>/dev/null || echo "unknown"))/memory/ 2>/dev/null

# Find the 5 most recent session files for this project
project_slug=$(pwd | sed 's|/|-|g' | sed 's|^-||')
ls -t ~/.claude/projects/${project_slug}/*.jsonl 2>/dev/null | head -5
```

Note:
- The `<!-- last-reviewed: -->` date from context.md
- Which sections exist (Service Stack, Known Constraints, Conventions)
- What's already documented so you don't duplicate it

### 2. Read memory topic files

For each file in the project's memory directory, read it and look for entries that describe:
- Software identity or API facts (what something actually is, not what was assumed)
- Infrastructure constraints (tool availability, network topology)
- Architectural decisions that should be stable facts

These may belong in context.md rather than (or in addition to) memory.

### 3. Read recent sessions

Read the 5 most recent session `.jsonl` files. For each file, scan for:
- Wrong-software discoveries: "not Synapse", "actually Conduit", "wrong API", "turned out"
- Tool-availability findings: "no curl", "no wget", "doesn't have bash", "minimal image"
- Architectural confirmations: "the convention is", "always goes in", "never edit directly"
- New services or roles mentioned that aren't in context.md's Service Stack

Read ONLY the context around matches — lines where `type` is `"human"` or `"assistant"`.

### 4. Build a proposed diff

For each finding NOT already in context.md:

**Format a proposed addition:**
```
Section: Known Constraints > Software identity
Evidence: [session date, quote]
Proposed addition:
  - <Fact.> <!-- added by /context YYYY-MM-DD -->
```

Present all proposed additions before writing anything. For each one, confirm before applying.

**Do not:**
- Remove or modify existing content
- Add entries already present (even if worded differently)
- Add session-behavior facts (preferences, decisions) — those belong in memory/

### 5. Apply confirmed additions

After user confirmation for each addition:
1. Insert the bullet or table row in the correct section
2. Update `<!-- last-reviewed: YYYY-MM-DD -->` to today at the top of the file

### 6. Summary

Print:
- How many candidates were found
- How many were added (vs. skipped or deferred)
- New `last-reviewed` date

---

## When to use

- After adding a new Ansible role / service to a homelab-style project
- After a session where Claude assumed the wrong software (e.g., Synapse instead of Conduit)
- When `last-reviewed` date is >14 days old and work is active
- After discovering a container lacks a tool you assumed was present

## Output location

`.claude/context.md` in the project root (committed, human-maintained).
