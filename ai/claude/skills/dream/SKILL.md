---
name: dream
description: "Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. Inspired by how sleep consolidates human memory."
---

# Dream - Memory Consolidation

Consolidates scattered auto-memory notes into a clean, organized knowledge base by scanning recent session transcripts for corrections, decisions, preferences, and patterns.

Run manually with `/dream`. Auto-triggers every 24 hours via the Stop hook managed by `otto-workbench sync`.

---

## How It Works

Dream runs 5 sequential phases. Execute them in order. Do not skip phases.

```
ORIENT --> GATHER SIGNAL --> CONSOLIDATE --> PRUNE & INDEX --> CONTEXT UPDATE
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

**Review feedback signals:**
```bash
grep -il "accepted\|rejected\|won't-fix\|false positive\|not applying\|review feedback summary" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
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
   - `review-feedback.md` — Accepted/rejected review findings and false positive patterns
   - Create new topic files only when existing ones don't fit

6. **Route facts to the right destination — memory, context.md, or machine.md.**

   **machine.md** (`~/.claude/machine/machine.md`) — facts about the machine itself:
   - OS behavior, runtime versions, tool paths, Docker state changes
   - Signs it belongs here: "Colima stopped", "upgraded to Node 22", "brew installed X", "not running"
   - Flag for Phase 5 (Machine Update) rather than writing to memory or context.md

   **context.md** (`.claude/context.md` in the project) — stable project architectural truth:
   - Software identity (what software actually runs), container tool constraints, conventions
   - Signs it belongs here: "X is not Y", "doesn't have curl", "use conduit.toml not homeserver.yaml"
   - Flag for Phase 5 (Context Update)

   **memory/** — session-derived behavior and preferences:
   - Signs it belongs here: "user prefers", "we decided", "stop doing", "next time"

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
5. Print a summary: entries added, updated, archived, contradictions resolved, context.md updates made

---

## Phase 5: CONTEXT UPDATE

**Goal:** Push architectural facts discovered in sessions into `.claude/context.md`.
Memory captures behavior; context.md captures stable truth about the project.

### When to run

Only if `.claude/context.md` exists in the project directory. Skip silently otherwise.

### What to scan for

Re-use the session files already scanned in Phase 2. Look specifically for architectural signals:

**Software/API identity discoveries:**
```bash
grep -il "not synapse\|not synapse\|actually uses\|it's actually\|turned out\|the real\|wrong api\|wrong image\|wrong software" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

**Container constraint discoveries:**
```bash
grep -il "not installed\|not available\|doesn't have\|missing tool\|no curl\|no wget\|no bash\|no shell\|minimal image\|distroless" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

**Architectural convention confirmations:**
```bash
grep -il "the convention is\|the pattern is\|always goes in\|never edit directly\|single source\|canonical location" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

### Confidence threshold

- **Two or more sessions** mention the same fact → add it directly to context.md
- **One session only** → add as an HTML comment `<!-- candidate: <fact> (seen: YYYY-MM-DD) -->` at the bottom of the relevant section for human review
- **Already in context.md** → skip (never duplicate)

### What to write

For each qualifying fact, determine the right section in context.md:
- Software/API identity → add to **Known Constraints** under "Software identity"
- Container tool absence → add to **Known Constraints** under "Container tool availability"
- Convention confirmed or corrected → add to **Conventions** section
- New service discovered → add a row to the **Service Stack** table (role, container, software, port, notes)

Format for new bullets:
```markdown
- <Fact stated concisely.> <!-- added by dream YYYY-MM-DD -->
```

### After writing

1. Update the `<!-- last-reviewed: YYYY-MM-DD -->` header at the top of context.md to today's date
2. Do NOT remove existing content — only append
3. If context.md was updated, note it in the Phase 5 summary line

---

## Phase 5b: MACHINE UPDATE

**Goal:** Push machine-level facts discovered in sessions into `~/.claude/machine/machine.md`.

### When to run

Only if `~/.claude/machine/machine.md` exists. Skip silently otherwise.

### What to scan for

Re-use the session files already scanned in Phase 2. Look for machine-level signals:

**Runtime/tool changes:**
```bash
grep -il "upgraded\|installed\|updated\|removed\|uninstalled\|now running\|switched to\|brew install" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

**Docker/runtime state changes:**
```bash
grep -il "colima\|docker.*not\|docker.*stopped\|socket.*not found\|docker desktop" ~/.claude/projects/*/sessions/*.jsonl 2>/dev/null
```

### What to write

machine.md is auto-generated — do NOT directly edit its sections.
Instead, append a `<!-- session-note: -->` comment at the bottom of the relevant section:

```markdown
<!-- session-note: <fact> (seen: YYYY-MM-DD) — run /machine to regenerate -->
```

This flags the section for refresh on the next `/machine` run, without corrupting the generated content.

### After writing

If machine.md was updated, note it in the Phase 5 summary line. Suggest running `/machine` to regenerate from current system state.
