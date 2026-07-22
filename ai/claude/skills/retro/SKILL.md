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
false negatives in coding rules. Proposes rule changes at the right level: global
rules in `ai/guidelines/rules/` for cross-project patterns, project rules in each
repo's `CLAUDE.md` for repo-specific conventions.

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

To override the scan window (e.g. for debugging or historical analysis):
```bash
retro-scan --since 7d
```

The report contains:
- **PR Comments by Repo** — substantive review comments from merged PRs since
  last retro, each with: author, body, file/line, direction, nearest matching
  rule (or "none"), and per-repo unmatched comment counts
- **Rules Coverage Summary** — per-rule-file counts of matched comments and gaps
- **Repeated Themes** — rules that matched 2+ comments across different PRs,
  with example comments (use these to prioritize classification)

Comment directions:
- `received` — feedback on the user's PR from a reviewer
- `gave` — feedback the user left on someone else's PR
- `observed` — feedback between other contributors

Self-replies (user commenting on their own PR) are automatically filtered out.
Duplicate comments between GitHub and local reviews are deduplicated.

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
| `project-rule` | Applies to one repo, not globally | Pattern is real and recurring but references project-internal packages, APIs, infra, or domain conventions |
| `one-off` | Context-specific, doesn't generalize | Feedback is about this specific PR's requirements or design choices |
| `noise` | Not actionable | Remaining style nits, questions answered in-thread, approval-adjacent |

### Scope discipline

"Repo-specific" is not "one-off." A comment is `one-off` only if the pattern
cannot recur — a design decision unique to one PR, a question answered in-thread.
If the feedback identifies a pattern that could recur in the same repo (e.g.,
"use the pagination helper," "don't rename Temporal activities"), classify it as
`project-rule` or `rule-gap`, not `one-off`.

### Priority elevation

- Comments that appear 2+ times across different PRs in the same category get
  elevated — these are patterns, not one-offs
- `false-negative` findings are high-priority — they mean existing rules aren't
  working, which is worse than missing rules
- `project-rule` findings with 2+ occurrences in the same repo are strong
  candidates — they indicate a real gap in that project's conventions

---

## Phase 3: PROPOSE

**Goal:** Draft concrete rule changes for each actionable finding.

### Rule placement

Before drafting a proposal, determine where the rule belongs. Walk this decision
tree for each finding — stop at the first match:

1. **References project-internal packages, APIs, or infrastructure?** (e.g.,
   `lib-go/pkg/pagination`, Temporal activities, specific DB schemas, internal
   service names) → **Project rule** for that repo's `CLAUDE.md`
2. **Language-general pattern observed only in one project's domain?** (e.g.,
   "verify covering index exists for query access patterns") → **Project rule**
   unless evidence spans 2+ repos, in which case promote to global
3. **Universal language or workflow pattern?** (e.g., Go import ordering,
   non-deterministic test assertions, error propagation) → **Global rule** in
   `ai/guidelines/rules/`
4. **Machine-specific tooling concern?** → **Machine-local rule** via
   `claude-rules add`

Project rules go in the target repo's `CLAUDE.md` under `## Conventions`. If the
repo has no `CLAUDE.md`, flag it in the report — retro does not create files, but
should note when one is needed.

### For `rule-gap` (global):
- Identify which rule file the new bullet should go in (or whether a new file is needed)
- Draft the rule text: one actionable bullet with rationale
- Specify where in the file it should be added (after which existing bullet)

### For `rule-gap` (project):
- Name the target repo and confirm placement is `CLAUDE.md` § Conventions
- Draft the rule text, stripping project-internal references that are obvious in
  context (e.g., don't repeat the package path if the repo only has one pagination helper)
- If the repo has no `CLAUDE.md`, note that one should be created first

### For `rule-refinement`:
- Quote the current rule text
- Show the proposed edit as old → new
- Explain what the current wording misses

### For `false-negative`:
- Quote the rule that should have prevented this
- Propose strengthening: add "MUST"/"NEVER", add an example, make language more specific
- Consider whether the rule is in a file that might not be loaded for the relevant file type (path-scoped frontmatter issue)

### For `project-rule`:
- Name the target repo
- Draft the rule text as a `CLAUDE.md` convention bullet
- If the finding also has a weaker global analog (e.g., "check for indexes" is
  global, but "use keyset pagination via X" is project-specific), propose both

Group proposals by placement tier (global first, then by target repo). Include
the source PR comment and PR number as evidence for each proposal.

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
<!-- prs-analyzed: N | findings: N | proposals: N (N global, N project) -->

## Global Proposals

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

## Project Proposals

### <repo-name> (CLAUDE.md)

#### N. <short description>
- **Evidence:** PR #N (@reviewer): "<comment text>"
- **Category:** project-rule | rule-gap
- **Proposed convention:**
  ```
  - <rule text>
  ```
- **Note:** <repo> has no CLAUDE.md — create one before applying (if applicable)

## Skipped (one-off / noise)
- PR #N: "<comment>" — <reason>
```

### Record timestamp and clean up

`retro-complete.sh` records the timestamp and cleans up only the review
directories that were scanned by `retro-scan` (listed in
`~/.config/workbench/retro-consumed-reviews.txt`).

```bash
~/.claude/skills/retro/retro-complete.sh
```

### Summary

Print a one-line summary:
```
Retro complete: N PRs analyzed, N findings (N global, N project), N proposals written.
Report: <workbench>/ai/memory/RETRO.md
```

---

## Safety

- **Read-only by default.** Retro never modifies rule files — it only writes the
  report. The user reviews and applies proposals manually.
- **Idempotent.** Running retro twice overwrites RETRO.md with fresh results.
- **No auto-escalation.** Retro does not create issues, PRs, or branches —
  it produces a document for human review.
