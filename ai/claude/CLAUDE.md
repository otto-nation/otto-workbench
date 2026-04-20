# Claude Code

## Rules

Coding guidelines load automatically from `~/.claude/rules/` based on file type.

### Where rules live

| Location | Purpose |
|---|---|
| `ai/claude/CLAUDE.md` | Global rules — applies to all projects |
| `ai/guidelines/rules/*.md` | Language and domain rules — path-scoped via frontmatter |
| `claude-rules add <domain> "rule"` | Machine-specific local rules (not tracked) |
| `claude-rules project add "rule"` | Append a rule to the current repo's CLAUDE.md |

### When to add a rule

Only add a rule when Claude gets something wrong without it. For each existing rule, ask:
"Would removing this cause Claude to make mistakes?" If not, cut it.

- Do not restate what Claude already knows from training data (e.g., "Follow PEP 8", "Use const not var")
- Do not describe what's visible by reading the code
- Enforce critical rules mechanically (hooks, linters) — not just prose
- Be specific enough to verify: "Use pytest" not "Write good tests"

### How to write a rule

- One actionable statement per bullet — concrete enough to execute immediately
- Include rationale ("why") so Claude can generalize to edge cases
- Keep each file under 200 lines — long files cause rules to be ignored
- Use path-scoped frontmatter so rules only load when relevant files are touched

## Git Commit & PR Rules

Never mention Claude Code, AI assistance, or co-authorship in commit messages, PR descriptions, or any git artifacts.

## Auto Dream

If `~/.claude/.dream-pending` exists at session start, run `/dream` as a subagent in the background, then delete the flag: `rm ~/.claude/.dream-pending`. This is the memory consolidation system — it triggers automatically every 24 hours via a Stop hook.

## Auto Promote

If `~/.claude/.promote-pending` exists at session start, run `/promote` as a subagent in the background, then delete the flag: `rm ~/.claude/.promote-pending`. This reviews accumulated memories for promotion into workbench artifacts (lint rules, scripts, coding rules, hooks). Triggers automatically every 7 days via a Stop hook.

## Machine Profile

If `~/.claude/machine/machine.md` exists, read it at session start — it contains
hardware, OS, runtime versions, Docker setup, Git identity, and the project registry.
Use it to answer environment questions without re-discovering system state. Check
`<!-- last-updated -->`: if more than 7 days ago, mention it may be stale and suggest
running `/machine` to refresh. Auto-regenerates every 24h via Stop hook; run `/machine`
to force a refresh.

## Project Anatomy

If `.claude/anatomy.md` exists in the project, read it before exploring unfamiliar parts of the codebase. It contains a file index with descriptions and token estimates — use it to decide which files to open instead of browsing blindly. Regenerated automatically via Stop hook; run `/anatomy` to force a refresh.

If `.claude/context.md` exists in the project, read it alongside anatomy.md — it contains architecture narrative, service identity, and known constraints that anatomy.md does not capture. Check the `<!-- last-reviewed: -->` date at the top: if it is more than 30 days ago, note that context.md may be stale. When working on infrastructure tasks, explicitly state which service you are targeting and confirm its software identity against context.md before writing any tasks or config.

## Agent Protocols

When a situation matches an agent's domain, read the agent file and follow its
protocol before taking action. Agent files live at `~/.claude/agents/`.

| Situation | Agent file | Constraint |
|-----------|-----------|------------|
| Investigating a bug, test failure, or unexpected behavior | `debugger.md` | Diagnose before fixing |
| Production incident or outage triage | `incident.md` | Read-only investigation |
| Dependency upgrade or framework migration | `migrate.md` | Plan before changing |
| Code review (PR or diff) | `reviewer.md` | Review before approving |

## Output

- Do not summarize changes at the end of a response — the diff speaks for itself
- When presenting options, keep descriptions to 1-2 sentences each
