---
name: dream
description: "Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. Inspired by how sleep consolidates human memory."
---

# Dream - Memory Consolidation

Consolidates scattered auto-memory notes into a clean, organized knowledge base by scanning recent session transcripts for corrections, decisions, preferences, and patterns.

Run manually with `/dream`. Auto-triggers every 24 hours via the Stop hook managed by `otto-workbench sync`.

---

## How It Works

Dream runs 4 sequential phases. Execute them in order. Do not skip phases.

```
ORIENT --> GATHER SIGNAL --> CONSOLIDATE --> PRUNE & INDEX
```

---

## Phase 1: ORIENT

**Goal:** Understand the current state of memory before changing anything.

1. Find memory directories:
```bash
ls -d ~/.claude/projects/*/memory/ 2>/dev/null
```

2. Read `MEMORY.md` in each project's memory directory. Note:
   - How many topic files exist
   - Total line count of MEMORY.md
   - Last modified dates
   - Any entries that look stale (relative dates like "yesterday" with no anchor)

3. Read each topic file to understand what's already stored.

### Output
A mental map of which projects have memory, what topics are covered, how large the files are, and what's potentially stale or contradictory.

---

## Phase 2: GATHER SIGNAL

**Goal:** Extract important information from recent sessions using targeted grep.

### Where to find transcripts
```bash
find ~/.claude/projects/*/sessions/ -name "*.jsonl" -mtime -7 2>/dev/null | sort -t/ -k6 -r
```

### What to scan for

**User corrections** (highest priority):
```bash
grep -il "actually\|no,\|wrong\|incorrect\|not right\|stop doing\|don't do\|I said\|I meant\|that's not\|correction" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

**Preferences and configuration:**
```bash
grep -il "I prefer\|always use\|never use\|I like\|I don't like\|I want\|from now on\|going forward\|remember that\|keep in mind\|make sure to\|default to" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

**Important decisions:**
```bash
grep -il "let's go with\|I decided\|we're using\|the plan is\|switch to\|move to\|chosen\|picked\|decision\|we agreed" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

**Recurring patterns:**
```bash
grep -il "again\|every time\|keep forgetting\|as usual\|same as before\|like last time\|we always\|the usual" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

### How to read matches

For each file that matches, read ONLY the surrounding context of the match. JSONL files have one JSON object per line. Focus on lines where `type` is `"human"` (user messages) and the immediately following `"assistant"` response.

### What to extract

For each finding, note:
- **The fact** — What was said or decided
- **The date** — Derive from the session file's modification time (use absolute dates)
- **Confidence** — Explicit instruction (high) or implied preference (medium)?
- **Contradictions** — Does this conflict with anything currently in memory?

---

## Phase 3: CONSOLIDATE

**Goal:** Merge new findings into existing memory.

### Rules

1. **Never duplicate.** Check if it already exists. If it does, update the existing entry.

2. **Convert relative dates to absolute.** "Yesterday" in a session from March 15 becomes "2026-03-14". Never store relative dates.

3. **Delete contradicted facts.** If memory says "Prefers tabs" but a recent session says "Use spaces", remove the old entry and write the new one. Add a note: `(Updated YYYY-MM-DD, previously: tabs)`.

4. **Preserve source attribution.** Note where each new entry came from: `(from session YYYY-MM-DD)`.

5. **Topic file organization.** Group related memories:
   - `preferences.md` — How the user likes things done
   - `decisions.md` — Choices and their rationale
   - `corrections.md` — Things the user corrected
   - `patterns.md` — Recurring workflows, common tasks
   - `facts.md` — Project-specific knowledge, architecture notes
   - Create new topic files only when existing ones don't fit

6. **Entry format.** Each memory entry should be concise:
```markdown
- [YYYY-MM-DD] The fact or preference. (source: session, confidence: high/medium)
```

---

## Phase 4: PRUNE & INDEX

**Goal:** Keep MEMORY.md lean. Remove stale content. Enforce size limits.

### MEMORY.md rules

MEMORY.md is an **index file**, not a content store:
- One-line summaries linking to topic files
- Never exceeds 200 lines
- No duplicate content from topic files

### Prune stale entries

Remove or archive entries that are:
- More than 90 days old with no references in recent sessions
- Contradicted by newer entries
- About projects that no longer exist in `~/.claude/projects/`

### Record the dream timestamp

After completing all 4 phases:
```bash
date +%s > ~/.claude/projects/<project>/memory/.last-dream
rm -f ~/.claude/.dream-pending
```

---

## Safety

- **Never delete memory without replacement.** Removed entries must be either contradicted (replaced) or moved (to topic file or archive).
- **Back up before first run.** On the very first run against a project:
```bash
cp -r ~/.claude/projects/<project>/memory/ ~/.claude/projects/<project>/memory-backup-$(date +%Y%m%d)/
```
- **Dry run option.** On first use, read through all 4 phases but only print what you WOULD change, without writing. Confirm with the user before applying.

## Verification

After running, verify:
1. `wc -l` on MEMORY.md — should be under 200 lines
2. No topic file has duplicate entries
3. No relative dates remain ("yesterday", "last week", etc.)
4. All topic files referenced in MEMORY.md actually exist
5. Print a summary: entries added, updated, archived, contradictions resolved
