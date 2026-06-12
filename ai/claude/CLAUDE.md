# Claude Code

## Git Commit & PR Rules

Never mention Claude Code, AI assistance, or co-authorship in commit messages, PR descriptions, or any git artifacts.

## Auto Dream

If `~/.claude/.dream-pending` exists at session start, run `/dream` as a subagent in the background, then delete the flag: `rm ~/.claude/.dream-pending`. This is the memory consolidation system — it triggers automatically every 24 hours via a Stop hook.

## Auto Promote

If `~/.claude/.promote-pending` exists at session start, run `/promote` as a subagent in the background, then delete the flag: `rm ~/.claude/.promote-pending`. This reviews accumulated memories for promotion into workbench artifacts (lint rules, scripts, coding rules, hooks). Triggers automatically every 7 days via a Stop hook.

## Auto Retro

If `~/.claude/.retro-pending` exists at session start, run `/retro` as a subagent in the background, then delete the flag: `rm ~/.claude/.retro-pending`. This analyzes recent PR review comments to identify gaps in coding rules. Triggers automatically every 72 hours via a Stop hook.

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

## claude-review Development

When adding or modifying a review phase, verify these integration points:
- `review-orchestrate`: `FINDING_SECTIONS` list, `SECTION_*` and `FINDING_PREFIX_*` constants, `renumber_section()`, `merge_reviews()`, `build_prompt()` template rendering
- `review-post`: `SECTION_HEADERS`, `SEVERITY_LABELS`, `renumber_for_posting()`, `parse_findings()` parser
- `agents/reviewer.md`: output format (Phase 10 markdown template), finding ID patterns (`[M1]`, `[S1]`, etc.)
- `lib/review-templates/`: section headers referenced in synthesis and group templates

## Output

- Do not summarize changes at the end of a response — the diff speaks for itself
- When presenting options, keep descriptions to 1-2 sentences each
