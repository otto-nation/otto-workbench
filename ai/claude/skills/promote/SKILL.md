---
name: promote
description: "Reviews accumulated Claude Code memories for promotion into durable workbench artifacts — lint rules, scripts, coding rules, hooks. Prioritizes mechanical enforcement over prose. TRIGGER when: user wants to review memories for promotion, or after dream has consolidated corrections. SKIP: direct rule/script edits — just edit them; memory consolidation (use dream)."
source: otto-workbench/ai/claude/skills/promote/SKILL.md
invocation: "/promote"
trigger: "Run to evaluate accumulated memories for promotion into workbench artifacts, or after dream has consolidated several sessions of corrections and decisions. Auto-triggers every 7 days."
skip: "Do not use when the user wants to directly edit a rule or script — just edit it. Do not use for memory consolidation (use dream instead)."
output: "ai/memory/PROMOTE.md"
lifecycle_cadence: "7 days"
lifecycle_scope: per-project
---

# Promote — Memory-to-Artifact Pipeline

Reviews accumulated Claude Code memories across all projects and evaluates whether
each one should be promoted into a durable workbench artifact. Prioritizes lint rules
and scripts (mechanical enforcement) over prose rules.

Run manually with `/promote`. Auto-triggers every 7 days via the Stop hook managed
by `otto-workbench sync`.

---

## How It Works

Promote runs 4 sequential phases. Execute them in order. Do not skip phases.

```
ORIENT --> EVALUATE --> PROPOSE --> RECORD
```

---

## Phase 1: ORIENT

**Goal:** Understand all accumulated memories and the current workbench state.

Run `promote-scan` to produce a structured report:
```bash
promote-scan
```

The report contains three sections:
- **Memory State** — per-project summary: MEMORY.md line count, topic files with names/descriptions/types/ages, full body content (up to 500 chars), stale entries (>90 days), last promote timestamp
- **Backed-Up Memories** — archived memory files from the workbench `ai/memory/` directory with the same detail
- **Workbench Artifacts** — current inventory of rules (filename, heading, and body content), scripts, hooks (event + matcher + command), and agents (filename + heading)

Use this report as input for Phase 2. Note any memories that look like they're describing
the same pattern across different projects.

---

## Phase 2: EVALUATE

**Goal:** Classify each memory into a promotion category or skip it.

For each memory entry, evaluate it against this priority ladder (first match wins):

### Priority 1: Lint rule or static check

Can this be enforced by a linter, formatter, or static analysis tool?

**Signs it belongs here:**
- "Always format X as Y"
- "Never use X pattern"
- A bug that a linter would have caught
- A style violation that recurred

**Target:** shellcheck directive, golangci-lint config, pre-commit hook, or
a note to configure an existing tool.

### Priority 2: Script or automation

Can this be automated as a bin script, task, or hook?

**Signs it belongs here:**
- A manual multi-step process described repeatedly
- "Every time I need to X, I have to Y then Z"
- A workflow that Claude kept being asked to perform

**Target:** New script in `bin/`, new task, or new hook in settings.json.

### Priority 3: Coding rule

Should this be a rule in `ai/guidelines/rules/`?

**Signs it belongs here:**
- A correction that Claude needed but wouldn't know from training data
- A project-specific convention that can't be inferred from code
- A pattern that applies across multiple files or projects

**Target:** New rule file or addition to existing rule file in `ai/guidelines/rules/`.
Use path-scoped frontmatter when the rule only applies to specific file types.

### Priority 4: Agent or skill update

Should an existing agent or skill be modified?

**Signs it belongs here:**
- A correction to how Claude performs reviews, debugging, or migrations
- A workflow step that's consistently missing from an agent protocol

**Target:** Edit to an existing agent `.md` or skill `SKILL.md`.

### Priority 5: Keep as memory

The memory is valuable context but doesn't generalize into an artifact.

**Signs it belongs here:**
- Project-specific decisions that don't apply elsewhere
- User preferences already well-served by existing rules
- Temporal context (deadlines, ongoing incidents)

**Action:** Leave in memory. No promotion needed.

### Priority 6: Delete

The memory is stale, redundant, or already covered by an existing artifact.

**Signs it belongs here:**
- A rule in the Workbench Artifacts → Rules section already states the same guidance — compare the memory's body content against rule body content shown in the report
- The rule/script/hook already exists
- The memory describes something that changed and is no longer true
- The memory is about a one-time task that's complete

**Action:** Flag for deletion (dream will clean it up on next run).

### Classification rules

- A memory that appears in 2+ projects is a stronger promotion candidate
- Memories tagged as `feedback` or `corrections` type are highest-value signals
- If unsure between Priority 3 (rule) and Priority 5 (keep), keep — false promotions
  create rule bloat
- Never promote a memory that contradicts an existing artifact without flagging the conflict

---

## Phase 3: PROPOSE

**Goal:** Write a promotion report with concrete, actionable proposals.

Write the report to `~/git/personal/otto-nation/otto-workbench/ai/memory/PROMOTE.md`.

### Report format

```markdown
# Memory Promotion Report
<!-- generated: YYYY-MM-DD -->
<!-- memories evaluated: N -->
<!-- promotions proposed: N -->

## Proposed Promotions

### P1: Lint Rules & Static Checks

#### <Title>
- **Source:** <project> / <memory file> / <entry>
- **Proposal:** <what to add/configure>
- **Target:** <specific file or tool config>
- **Rationale:** <why mechanical enforcement is better than prose>

### P2: Scripts & Automation

#### <Title>
- **Source:** <project> / <memory file> / <entry>
- **Proposal:** <what to automate>
- **Target:** `bin/<script-name>` or task or hook
- **Rationale:** <why automation beats manual repetition>

### P3: Coding Rules

#### <Title>
- **Source:** <project> / <memory file> / <entry>
- **Proposal:** <rule text>
- **Target:** `ai/guidelines/rules/<file>.md` (new or existing)
- **Rationale:** <why Claude needs this rule>

### P4: Agent/Skill Updates

#### <Title>
- **Source:** <project> / <memory file> / <entry>
- **Proposal:** <what to change>
- **Target:** `ai/claude/agents/<file>.md` or `ai/claude/skills/<dir>/`
- **Rationale:** <what's missing or wrong in current protocol>

## Skipped

| Memory | Project | Reason |
|--------|---------|--------|
| <entry> | <project> | Keep as memory / Already covered / Stale |

## Conflicts

List any proposed promotions that conflict with existing artifacts.
```

### Quality checks

Before writing the report:
- Verify every proposed target file exists (or note it needs to be created)
- Ensure no proposal duplicates an existing artifact
- Confirm the rule text follows workbench conventions (one actionable bullet,
  includes rationale, path-scoped frontmatter where appropriate)
- Keep proposals concise — the report is a menu for the user, not implementation docs

---

## Phase 4: RECORD

**Goal:** Record the promote timestamp and clean up.

```bash
bash ~/.claude/skills/promote/promote-complete.sh
```

### Summary

Print a one-line summary:
```
Promote complete: N memories evaluated, N promotions proposed (P1: N, P2: N, P3: N, P4: N), N skipped, N conflicts.
Report: ~/git/personal/otto-nation/otto-workbench/ai/memory/PROMOTE.md
```

---

## Safety

- **Read-only by default.** Promote never modifies workbench artifacts — it only
  writes the report. The user reviews and applies promotions manually.
- **No memory deletion.** Promote flags stale memories but never deletes them.
  Dream handles cleanup on its next cycle.
- **Idempotent.** Running promote twice produces the same report (overwrites PROMOTE.md).
