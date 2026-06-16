---
name: machine
description: "Refresh the machine profile (~/.claude/machine/machine.md) — hardware, OS, runtimes, Docker, Git identity, and project registry. TRIGGER when: user upgrades tools, installs new runtimes, or machine.md is stale (>7 days). SKIP: project-specific context (use context); memory consolidation (use dream)."
source: otto-workbench/ai/claude/skills/machine/SKILL.md
invocation: "/machine"
trigger: "Run after upgrading runtimes, installing new tools, or when machine.md last-updated is more than 7 days old. Auto-triggers every 24h."
skip: "Do not use for project-specific context (use context instead) or memory consolidation (use dream instead)."
output: "~/.claude/machine/machine.md"
lifecycle_cadence: "24h"
lifecycle_scope: global
---

# Machine — Machine Profile Refresh

Regenerates `~/.claude/machine/machine.md` unconditionally, bypassing the 24h
staleness check. Shows what changed from the previous profile.

Run manually with `/machine` after upgrading runtimes, installing new tools,
or when `<!-- last-updated -->` in machine.md is more than 7 days old.

---

## Steps

1. **Regenerate with diff**
```bash
bash ~/.claude/skills/machine/generate-machine-profile.sh --force --diff
```

2. **Print result**

Read and display `~/.claude/machine/machine.md`.

---

## Output location

`~/.claude/machine/machine.md` — read at every session start via global CLAUDE.md.
Auto-regenerates via Stop hook every 24h. Backed up to workbench `ai/memory/machine/`
on every `otto-workbench sync`.
