---
paths:
  - "**/CLAUDE.md"
  - "**/rules/**"
  - "**/guidelines/**"
---

# Rules Authoring

## Where rules live

| Location | Purpose |
|---|---|
| `ai/claude/CLAUDE.md` | Claude Code's own instructions — agent protocols. Not read by other harnesses |
| `ai/guidelines/rules/*.md` | Language and domain rules — path-scoped via frontmatter |
| `claude-rules add <domain> "rule"` | Machine-specific local rules (not tracked) |
| `claude-rules project add "rule"` | Append a rule to the current repo's CLAUDE.md |

## When to add a rule

Only add a rule when Claude gets something wrong without it. For each existing rule, ask:
"Would removing this cause Claude to make mistakes?" If not, cut it.

- Do not restate what Claude already knows from training data (e.g., "Follow PEP 8", "Use const not var")
- Do not describe what's visible by reading the code
- Enforce critical rules mechanically (hooks, linters) — not just prose
- Be specific enough to verify: "Use pytest" not "Write good tests"

## How to write a rule

- One actionable statement per bullet — concrete enough to execute immediately
- Include rationale ("why") so Claude can generalize to edge cases
- Keep each file under 200 lines — long files cause rules to be ignored
- Use path-scoped frontmatter so rules only load when relevant files are touched

## Which harnesses a rule reaches

Claude Code loads a directory of rules and scopes them by path. Pi loads one
context file and cannot scope at all, so `ai/pi/steps.sh` generates that file
from the always-on rules only.

| Frontmatter | Claude Code | Pi |
|---|---|---|
| none | always | always |
| `paths: [...]` | when a matching file is touched | never — Pi has no path scoping |
| `harness: [claude]` | always | never |
| `harness: [claude, pi]` | always | always |

Adding `paths:` therefore takes a rule away from Pi entirely. For guidance that
must reach both and only sometimes applies, write a skill instead — description
matching is the one conditional load both harnesses share.

`harness:` is for a rule whose *content* is harness-specific, not for turning a
rule off. `bash-tool.md` is the only one today: it catalogues Claude Code's
static analyzer, and under another harness it forbids commands that were never
going to prompt.
