---
name: architecture
description: "On-demand architecture.md refresh. Reads recent sessions and memory to identify architectural facts that are missing or stale, then proposes specific additions to .agents/architecture.md. TRIGGER when: user discovers wrong-software assumptions, adds a new service or role, or architecture.md is stale (last-reviewed >14 days). SKIP: memory consolidation (use dream); machine-level facts (use machine)."
source: otto-workbench/ai/skills/architecture/SKILL.md
invocation: "/architecture"
trigger: "Run after discovering wrong-software assumptions, adding a new service or role to a project, when architecture.md last-reviewed date is more than 14 days old, or after discovering container tool constraints."
skip: "Do not use for memory consolidation (use dream instead) or machine-level facts (use machine instead)."
output: ".agents/architecture.md"
---

# Architecture — Project Architecture Refresh

Refreshes `.agents/architecture.md` with architectural facts discovered in recent sessions.
Lighter than `/dream` — focuses only on architecture.md, not memory consolidation.

Run manually with `/architecture` after adding a new service, discovering a wrong-API assumption,
or when `<!-- last-reviewed -->` is more than 14 days old and active work is ongoing.

Writes go to `.agents/architecture.md`. Do not write `.claude/architecture.md`.

---

## Steps

### 1. Resolve the project root

```bash
git rev-parse --show-toplevel
resolve-worktree
```

`architecture.md` is a tracked project file, so it belongs in a working tree. See
`ai/skills/analyze-project/SKILL.md` step 1a for the full contract behind these
two commands — which one wins, what each `resolve-worktree` exit code means, and why
this never falls back to the current directory. A container holds no work tree, so an
`architecture.md` written there is covered by no `.gitignore` rule, reaches no review,
and is seen by no CI check — the only way it is ever found is by hand.

`cd` to the resolved path and run every step below from there.

### 2. Read current state

```bash
repo=$(git rev-parse --show-toplevel)

if [[ -f .agents/architecture.md ]]; then
  cat .agents/architecture.md
elif [[ -f .claude/architecture.md ]]; then
  echo "legacy: .claude/architecture.md — propose moving to .agents/architecture.md; do not write here"
  cat .claude/architecture.md
else
  echo "FIRST RUN: no architecture.md"
fi

mem=$(dream-scan --memory-dir "$repo")
if [[ -d "$mem" ]]; then
  ls "$mem"
else
  echo "none"
fi

dream-scan --list-transcripts --days 30 --repo "$repo" | head -5
```

If `docs/architecture.md` exists, read it as product architecture (install/sync,
components). Different audience from `.agents/architecture.md` — do not copy it.

Note:
- Missing `.agents/architecture.md` is a first-run, not a crashed step
- Missing memory directory is `none`, not "no memory to consider"
- The `<!-- last-reviewed: -->` date from architecture.md when the file exists
- Which sections exist (Service Stack, Known Constraints, Conventions)
- What's already documented so you don't duplicate it

### 3. Read memory topic files

If step 2 printed `none`, skip this step.

For each file in the project's memory directory, read it and look for entries that describe:
- Software identity or API facts (what something actually is, not what was assumed)
- Infrastructure constraints (tool availability, network topology)
- Architectural decisions that should be stable facts

These may belong in architecture.md rather than (or in addition to) memory.

### 4. Read recent sessions

The list from step 2 is already this repo. Extract user turns first (grep, or a
short Python pass over `type=user` / Pi `role=user` records). Do not dispatch a
Carbon (or other) subagent at a pile of jsonl — observed 2026-11-19 through Pi's
`subagent` tool: killed at 300s with no output. Revisit when the model or stall
timeout changes.

The two harnesses write different record shapes — Claude Code writes
`{"type": "user", ...}` and Pi writes `{"type": "message", "message":
{"role": "user"}}`. Scanning the raw text for the phrases below reads both
without having to tell them apart; `ai/lib/core/sessions.py` is what parses
them properly when structure is needed.

For each file, scan for:
- Wrong-software discoveries: "not Synapse", "actually Conduit", "wrong API", "turned out"
- Tool-availability findings: "no curl", "no wget", "doesn't have bash", "minimal image"
- Architectural confirmations: "the convention is", "always goes in", "never edit directly"
- New services or roles mentioned that aren't in architecture.md's Service Stack

Read ONLY the context around matches.

### 5. Build a proposed diff

First-run (no architecture.md, or only the legacy `.claude/` copy): propose a
new `.agents/architecture.md` (software identity, a pointer at
`docs/architecture.md` when that file exists, known constraints). Do not copy
product docs into it.

For each finding NOT already in architecture.md:

**Format a proposed addition:**
```
Section: Known Constraints > Software identity
Evidence: [session date, quote]
Proposed addition:
  - <Fact.> <!-- added by /architecture YYYY-MM-DD -->
```

Present all proposed additions before writing anything. For each one, confirm before applying.

**Do not:**
- Remove or modify existing content (except a first-run move off `.claude/`)
- Add entries already present (even if worded differently)
- Add session-behavior facts (preferences, decisions) — those belong in memory/
- Treat an unapproved UI preference as architecture

### 6. Apply confirmed additions

After user confirmation for each addition:
1. Write `.agents/architecture.md` (create it on first-run; never write `.claude/architecture.md`)
2. Insert the bullet or table row in the correct section
3. Update `<!-- last-reviewed: YYYY-MM-DD -->` to today at the top of the file

### 7. Summary

Print:
- How many candidates were found
- How many were added (vs. skipped or deferred)
- New `last-reviewed` date
- FIRST RUN when the file was created

---

## When to use

- After adding a new Ansible role / service to a homelab-style project
- After a session where Claude assumed the wrong software (e.g., Synapse instead of Conduit)
- When `last-reviewed` date is >14 days old and work is active
- After discovering a container lacks a tool you assumed was present

## Output location

`.agents/architecture.md` in the project root (committed, human-maintained) — the root
step 1 resolves, which for a session rooted at a bare-repo container is the worktree
that container stands in for, never the container.
