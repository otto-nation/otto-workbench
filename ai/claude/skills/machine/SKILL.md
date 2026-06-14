---
name: machine
description: "Refresh the machine profile (~/.claude/machine/machine.md) — hardware, OS, runtimes, Docker, Git identity, and project registry. Run after upgrading tools or to force a refresh."
source: otto-workbench/ai/claude/skills/machine/SKILL.md
lifecycle_cadence: "24h"
lifecycle_scope: global
lifecycle_output: "~/.claude/machine/machine.md"
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
