# Generated Project Artifacts

Context files other tools in this workbench regenerate for you. Each is written
on a schedule and read at session start; none is authored by hand.

## Machine Profile

If `~/.claude/machine/machine.md` exists, read it at session start — it contains
hardware, OS, runtime versions, Docker setup, Git identity, and the project registry.
Use it to answer environment questions without re-discovering system state. Check
`<!-- last-updated -->`: if more than 7 days ago, mention it may be stale and suggest
refreshing it. Claude Code owns the regeneration — a Stop hook rewrites the file every
24h and `/machine` there forces it — so under any other harness the file is whatever
Claude Code last wrote, and the staleness check is the part that matters.

## Project Anatomy

If `.claude/anatomy.md` exists in the project, read it before exploring unfamiliar parts of the codebase. It contains a file index with descriptions and token estimates — use it to decide which files to open instead of browsing blindly. Claude Code owns the regeneration — a Stop hook rewrites it and `/anatomy` there forces a refresh — so under any other harness, read it and say if it looks stale rather than trying to refresh it.

If `.claude/architecture.md` exists in the project, read it alongside anatomy.md — it contains architecture narrative, service identity, and known constraints that anatomy.md does not capture. Check the `<!-- last-reviewed: -->` date at the top: if it is more than 30 days ago, note that architecture.md may be stale. When working on infrastructure tasks, explicitly state which service you are targeting and confirm its software identity against architecture.md before writing any tasks or config.

## Ceiling Debt

If `.claude/ceiling-debt.md` exists in the project, read it at session start — it
lists deliberate simplifications marked with `// ceiling:` comments. Each entry names
the tradeoff and the upgrade trigger. Entries flagged **no-trigger** are rot risk;
entries flagged **permanent** are accepted for good and are not debt. Claude Code owns
the regeneration — a Stop hook rewrites it and `/ceiling-debt` there forces a refresh —
so under any other harness, read it and treat a missing entry as one nobody has scanned
for yet.

## Reuse Level

If `reuse.level` is set in `~/.config/workbench/config.yml`, read it at session
start — it controls how aggressively the reuse ladder (in `general.md`) is enforced:

| Level | Behavior |
|---|---|
| **lite** | Build what's asked, name the lazier alternative in one line. User picks |
| **full** | Enforce the reuse ladder. Stdlib and native first. Shortest diff (default) |
| **ultra** | Challenge the requirement. Deletion before addition. Ship the one-liner |

When `reuse.level` is unset, the level is the value of `reuse.default` in the
same file, and `full` when that is unset too. Change either with
`otto-workbench config set reuse.level ultra` (or `reuse.default`), which every harness
can run. Claude Code also has `/reuse lite|full|ultra` and
`/reuse default lite|full|ultra`, which do the same thing.
