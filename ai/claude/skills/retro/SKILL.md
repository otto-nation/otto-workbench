---
name: retro
description: "Analyze PR review comments to identify gaps in coding rules. Fetches comments from all registered repos, classifies them against existing rules, and proposes specific rule additions or refinements. TRIGGER when: user wants to analyze review patterns for rule gaps, after a batch of PR reviews. SKIP: addressing comments on a specific PR (use pr-comments); memory consolidation (use dream)."
source: otto-workbench/ai/claude/skills/retro/SKILL.md
invocation: "/retro"
trigger: "Run to analyze recent PR review comments for coding rule gaps, after a round of PR reviews has been completed, or when rule coverage feels incomplete. Auto-triggers every 72h."
skip: "Do not use when the user wants to address comments on a specific PR (use pr-comments instead). Do not use for memory consolidation (use dream instead)."
output: "ai/memory/RETRO.md"
lifecycle_cadence: "72h"
lifecycle_scope: global
---

# Retro — PR Review Feedback Loop

Analyzes PR review comments (human and bot) to identify gaps, weaknesses, and
false negatives in the coding rules at `ai/guidelines/rules/`. Proposes specific
rule additions, refinements, and strengthening.

Run manually with `/retro`. Auto-triggers every 3 days via the Stop hook managed
by `otto-workbench sync`.

---

## How It Works

Retro runs 4 sequential phases. Execute them in order. Do not skip phases.

```
ORIENT --> CLASSIFY --> PROPOSE --> REPORT
```

---

## Phase 1: ORIENT

**Goal:** Collect PR review comments and cross-reference against current rules.

Run `retro-scan` to produce a structured report:
```bash
retro-scan
```

The report contains:
- **PR Comments by Repo** — substantive review comments from merged PRs since
  last retro, each with: author, body, file/line, direction (received vs. gave),
  nearest matching rule (or "none")
- **Rules Coverage Summary** — per-rule-file counts of matched comments and gaps

Read all rule files referenced in the report to understand what each rule currently
covers before classifying.

If the scan report is empty (no PR comments found), skip to Phase 4 and record
the timestamp — there's nothing to analyze.

---

## Phase 2: CLASSIFY

**Goal:** Assign a category to each substantive PR comment.

For each comment in the scan report, assign exactly one category:

| Category | Meaning | When to assign |
|----------|---------|----------------|
| `rule-gap` | No existing rule covers this | Nearest rule is "none" or very weak match, and the comment identifies a generalizable pattern |
| `rule-refinement` | Existing rule is too vague or narrow | Nearest rule matches but doesn't address the specific concern |
| `false-negative` | Rule exists but wasn't followed | Nearest rule clearly covers this, but the code still violated it |
| `one-off` | Context-specific, doesn't generalize | Feedback is about this specific PR's requirements or design choices |
| `noise` | Not actionable | Remaining style nits, questions answered in-thread, approval-adjacent |

### Priority elevation

- Comments that appear 2+ times across different PRs in the same category get
  elevated — these are patterns, not one-offs
- `false-negative` findings are high-priority — they mean existing rules aren't
  working, which is worse than missing rules

---

## Phase 3: PROPOSE

**Goal:** Draft concrete rule changes for each actionable finding.

For each non-skip finding, draft a proposal:

### For `rule-gap`:
- Identify which rule file the new bullet should go in (or whether a new file is needed)
- Draft the rule text: one actionable bullet with rationale
- Specify where in the file it should be added (after which existing bullet)

### For `rule-refinement`:
- Quote the current rule text
- Show the proposed edit as old → new
- Explain what the current wording misses

### For `false-negative`:
- Quote the rule that should have prevented this
- Propose strengthening: add "MUST"/"NEVER", add an example, make language more specific
- Consider whether the rule is in a file that might not be loaded for the relevant file type (path-scoped frontmatter issue)

Group proposals by target rule file. Include the source PR comment and PR number
as evidence for each proposal.

---

## Phase 4: REPORT

**Goal:** Write the report and record the timestamp.

Write `RETRO.md` to the workbench memory directory (`ai/memory/RETRO.md` relative
to the workbench root). Determine the workbench path from the `OTTO_WORKBENCH`
environment variable, or default to `~/git/personal/otto-nation/otto-workbench/main`.

### Report format

```markdown
# Retro Report
<!-- generated: YYYY-MM-DD -->
<!-- prs-analyzed: N | findings: N | proposals: N -->

## Proposals

### Rule Gaps (new rules needed)

#### 1. <rule-file> — <short description>
- **Evidence:** PR #N (@reviewer): "<comment text>"
- **Category:** rule-gap
- **Proposed addition:**
  ```
  - <rule text>
  ```
- **Suggested location:** After "<existing bullet text>" in <file>

### Rule Refinements

#### N. <rule-file> — <short description>
- **Evidence:** PR #N (@reviewer): "<comment text>"
- **Category:** rule-refinement
- **Current rule:** "<existing text>"
- **Proposed edit:** "<new text>"

### False Negatives (rule existed but wasn't followed)

#### N. <rule-file> — <short description>
- **Evidence:** PR #N (@reviewer): "<comment text>"
- **Category:** false-negative
- **Existing rule:** "<rule text>" in <file>
- **Proposed strengthening:** "<stronger version>"

## Skipped (one-off / noise)
- PR #N: "<comment>" — <reason>
```

### Record timestamp and clean up

```bash
~/.claude/skills/retro/retro-complete.sh
```

### Summary

Print a one-line summary:
```
Retro complete: N PRs analyzed, N findings (N rule-gaps, N refinements, N false-negatives), N proposals written.
Report: <workbench>/ai/memory/RETRO.md
```

---

## Safety

- **Read-only by default.** Retro never modifies rule files — it only writes the
  report. The user reviews and applies proposals manually.
- **Idempotent.** Running retro twice overwrites RETRO.md with fresh results.
- **No auto-escalation.** Retro does not create issues, PRs, or branches —
  it produces a document for human review.
