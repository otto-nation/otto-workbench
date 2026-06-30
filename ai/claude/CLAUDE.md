# Claude Code

## Git Commit & PR Rules

Never mention Claude Code, AI assistance, or co-authorship in commit messages, PR descriptions, or any git artifacts.

## Machine Profile

If `~/.claude/machine/machine.md` exists, read it at session start — it contains
hardware, OS, runtime versions, Docker setup, Git identity, and the project registry.
Use it to answer environment questions without re-discovering system state. Check
`<!-- last-updated -->`: if more than 7 days ago, mention it may be stale and suggest
running `/machine` to refresh. Auto-regenerates every 24h via Stop hook; run `/machine`
to force a refresh.

## Project Anatomy

If `.claude/anatomy.md` exists in the project, read it before exploring unfamiliar parts of the codebase. It contains a file index with descriptions and token estimates — use it to decide which files to open instead of browsing blindly. Regenerated automatically via Stop hook; run `/anatomy` to force a refresh.

If `.claude/architecture.md` exists in the project, read it alongside anatomy.md — it contains architecture narrative, service identity, and known constraints that anatomy.md does not capture. Check the `<!-- last-reviewed: -->` date at the top: if it is more than 30 days ago, note that architecture.md may be stale. When working on infrastructure tasks, explicitly state which service you are targeting and confirm its software identity against architecture.md before writing any tasks or config.

## Agent Protocols

When a situation matches an agent's domain, read the agent file and follow its
protocol before taking action. Agent files live at `~/.claude/agents/`.

| Situation | Agent file | Constraint |
|-----------|-----------|------------|
| Investigating a bug, test failure, or unexpected behavior | `debugger.md` | Diagnose before fixing |
| Production incident or outage triage | `incident.md` | Read-only investigation |
| Dependency upgrade or framework migration | `migrate.md` | Plan before changing |
| Code review (PR or diff) | `reviewer.md` | Review before approving |

## Reuse Level

If `~/.config/workbench/reuse-level` exists, read it at session start — it controls
how aggressively the reuse ladder (in `general.md`) is enforced:

| Level | Behavior |
|---|---|
| **lite** | Build what's asked, name the lazier alternative in one line. User picks |
| **full** | Enforce the reuse ladder. Stdlib and native first. Shortest diff (default) |
| **ultra** | Challenge the requirement. Deletion before addition. Ship the one-liner |

Default is `full` when no file exists. Change with `/reuse lite|full|ultra`.

## Ceiling Debt

If `.claude/ceiling-debt.md` exists in the project, read it at session start — it
lists deliberate simplifications marked with `// ceiling:` comments. Each entry names
the tradeoff and (ideally) the upgrade trigger. Entries flagged **no-trigger** are rot
risk. Auto-regenerated via Stop hook; run `/ceiling-debt` to force a refresh.

## Output

- Do not summarize changes at the end of a response — the diff speaks for itself
- When presenting options, keep descriptions to 1-2 sentences each
