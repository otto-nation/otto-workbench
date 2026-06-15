---
name: dream
description: "Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. Inspired by how sleep consolidates human memory."
source: otto-workbench/ai/claude/skills/dream/SKILL.md
lifecycle_cadence: "24h"
lifecycle_scope: per-project
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

## Phases 1+2: ORIENT & GATHER SIGNAL

**Goal:** Understand memory state and extract signals from recent sessions.

Run `dream-scan` to produce a structured report covering both phases:
```bash
dream-scan --days 7
```

The report contains two sections:
- **Memory State** — per-project summary: MEMORY.md line count, topic files with names/descriptions/ages, stale entries (>90 days), last dream timestamp
- **Session Signals** — user messages matching correction, preference, decision, pattern, and review feedback patterns, grouped by category with dates and project context

Use this report as input for Phase 3. Read the topic files referenced in the Memory State section to understand what's already stored before making changes.

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
bash ~/.claude/skills/dream/dream-complete.sh
```

---

## Safety

- **Never delete memory without replacement.** Removed entries must be either contradicted (replaced) or moved (to topic file or archive).
- **Back up before first run.** On the very first run against a project:
```bash
bash ~/.claude/skills/dream/dream-complete.sh --backup <project-slug>
```
- **Dry run option.** On first use, read through all 4 phases but only print what you WOULD change, without writing. Confirm with the user before applying.

## Verification

Run `dream-verify` to check memory integrity:
```bash
dream-verify
```

It validates: MEMORY.md under 200 lines, all references resolve, no relative dates in topic files, no duplicate `name:` frontmatter.

After verification, print a summary: entries added, updated, archived, contradictions resolved, context.md updates made.

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
grep -il "not synapse\|not synapse\|actually uses\|it's actually\|turned out\|the real\|wrong api\|wrong image\|wrong software" ~/.claude/projects/*/*.jsonl 2>/dev/null
```

**Container constraint discoveries:**
```bash
grep -il "not installed\|not available\|doesn't have\|missing tool\|no curl\|no wget\|no bash\|no shell\|minimal image\|distroless" ~/.claude/projects/*/*.jsonl 2>/dev/null
```

**Architectural convention confirmations:**
```bash
grep -il "the convention is\|the pattern is\|always goes in\|never edit directly\|single source\|canonical location" ~/.claude/projects/*/*.jsonl 2>/dev/null
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
grep -il "upgraded\|installed\|updated\|removed\|uninstalled\|now running\|switched to\|brew install" ~/.claude/projects/*/*.jsonl 2>/dev/null
```

**Docker/runtime state changes:**
```bash
grep -il "colima\|docker.*not\|docker.*stopped\|socket.*not found\|docker desktop" ~/.claude/projects/*/*.jsonl 2>/dev/null
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
