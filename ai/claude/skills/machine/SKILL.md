---
name: machine
description: "Refresh the machine profile (~/.claude/machine/machine.md) — hardware, OS, runtimes, Docker, Git identity, and project registry. Run after upgrading tools or to force a refresh."
---

# Machine — Machine Profile Refresh

Regenerates `~/.claude/machine/machine.md` unconditionally, bypassing the 24h
staleness check. Shows what changed from the previous profile.

Run manually with `/machine` after upgrading runtimes, installing new tools,
or when `<!-- last-updated -->` in machine.md is more than 7 days old.

---

## Steps

1. **Back up current profile**
```bash
cp ~/.claude/machine/machine.md ~/.claude/machine/machine.md.prev 2>/dev/null || true
```

2. **Regenerate**
```bash
bash ~/.claude/skills/machine/generate-machine-profile.sh --force
```

3. **Show diff**
```bash
diff ~/.claude/machine/machine.md.prev ~/.claude/machine/machine.md 2>/dev/null \
  || echo "(no previous profile to diff)"
rm -f ~/.claude/machine/machine.md.prev
```

4. **Print result**

Read and display `~/.claude/machine/machine.md`.

---

## Output location

`~/.claude/machine/machine.md` — read at every session start via global CLAUDE.md.
Auto-regenerates via Stop hook every 24h. Backed up to workbench `ai/memory/machine/`
on every `otto-workbench sync`.
