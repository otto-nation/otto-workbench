---
name: ceiling-debt
description: "Scan for ceiling: markers and present the debt ledger. TRIGGER when: user asks about ceilings, deferred simplifications, or technical debt markers. SKIP: general tech debt discussion without ceiling markers."
source: otto-workbench/ai/claude/skills/ceiling-debt/SKILL.md
invocation: "/ceiling-debt"
trigger: "ceiling debt, show ceilings, what did we defer, list simplifications, ceiling markers"
skip: "General tech debt discussion, architecture review, non-code requests"
output: "ceiling debt ledger to stdout (manual); .claude/ceiling-debt.md (auto)"
lifecycle_cadence: "on-stop"
lifecycle_scope: per-project
---

# Ceiling Debt

Scan for `// ceiling:` markers in the current repo and present a structured debt ledger.

Run manually with `/ceiling-debt`. Auto-regenerates on session stop via the Stop hook.

---

## Steps

1. Run `ceiling-scan` in the current repo root:
```bash
ceiling-scan
```

2. If no markers found (output shows `0 marker(s)`): report "No ceiling markers. Clean ledger." and stop.

3. Present the ledger grouped by file. Highlight entries with **no-trigger** as rot risk — these are deliberate simplifications with no documented upgrade condition.

4. For each **no-trigger** entry, suggest adding an upgrade trigger using `git blame` to identify the author:
```bash
git blame -L <line>,<line> <file>
```

5. If `.claude/ceiling-debt.md` exists and is stale (different from scan output), note it will be refreshed on session exit.
