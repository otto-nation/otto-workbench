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

### Workbench

Your developer environment is the repo containing this file (resolve via
`readlink ~/.claude/CLAUDE.md`). It owns Claude config (`ai/claude/`),
coding rules (`ai/guidelines/rules/`), bin scripts (`bin/`), zsh config (`zsh/`),
git config (`git/`), Docker setup (`docker/`), and Brew packages (`brew/`).

When modifying any of these — make the change in the workbench repo and re-run
the relevant setup script. Do not edit `~/` directly.

## Git Commit & PR Rules

Never mention Claude Code, AI assistance, or co-authorship in commit messages, PR descriptions, or any git artifacts.

## Auto Dream

If `~/.claude/.dream-pending` exists at session start, run `/dream` as a subagent in the background, then delete the flag: `rm ~/.claude/.dream-pending`. This is the memory consolidation system — it triggers automatically every 24 hours via a Stop hook.

## Output

- Do not summarize changes at the end of a response — the diff speaks for itself
- When presenting options, keep descriptions to 1-2 sentences each
