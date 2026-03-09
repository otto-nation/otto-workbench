# Claude Code

## Tools
- Code exploration: use Serena (prefer symbolic reads over full file reads)
- Multi-step decisions: use sequential-thinking
- Third-party library docs: use context7

## Rules
Coding guidelines load automatically from `~/.claude/rules/` based on file type.
- Add personal or machine-specific rules as `~/.claude/rules/<topic>.local.md`
- Add personal project rules as `.claude/rules/<topic>.local.md` (gitignored)
- Run `claude-rules add <domain> "rule"` to append a rule without opening files

## Workbench
Your developer environment is managed by the workbench repo.
See `~/.claude/rules/workbench.md` for location, ownership, and maintenance instructions.
