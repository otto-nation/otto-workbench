---
name: Decisions & Preferences
description: Recorded decisions, user preferences, and patterns from sessions
type: project
---

## Script Standards

- [2026-04-19] All custom bin scripts must implement `-h`/`--help` flags. Use `bin/template` as the starting point for new scripts. (source: session 222533c3, confidence: high)
- [2026-04-19] `bin/template` is the canonical scaffold for new scripts — sources `lib/ui.sh` via `_SELF` readlink pattern, defines `usage()` and `main()`, handles `-h|--help` and unknown options. (source: session 222533c3, confidence: high)

## Component Architecture

- [2026-03-24] E1/E2 architectural redesign complete: `install.sh` uses glob auto-discovery for core components (`*/steps.sh`), skipping dirs with `setup.conf` (= optional). Dependency framework added via `depends = brew` in `setup.conf`. (source: plan_audit_fixes_p2, confidence: high)
- [2026-03-24] All audit fix phases (1-5 from first pass, A-D from second pass) are complete. (source: plan_audit_fixes, plan_audit_fixes_p2, confidence: high)

## Tooling Removals

- [2026-04-17] Kiro AI tool removed from workbench entirely — `ai/kiro/` deleted, migration `20260417-remove-kiro.sh` applied. (source: git log d357403, confidence: high)
- [2026-04-17] `bin/claude-init` removed — migration `20260417-remove-claude-init.sh` applied. (source: git log d357403, confidence: high)
- [2026-04-17] iTerm2 support removed — `terminals/iterm/` deleted. (source: git log d357403, confidence: high)

## Known Issues / Recurring Warnings

- [2026-04-19] `otto-workbench sync` emits warning if `mise` is not installed — expected on machines without mise. (source: session 4c8d66b3, confidence: high)
- [2026-04-19] `otto-workbench sync` warns if `~/.gitconfig` already exists — expected; workbench appends/includes rather than overwriting. (source: session 4c8d66b3, confidence: high)
- [2026-04-19] `ai/claude/mcps` directory must exist with MCP config files or sync warns "No MCP configs found". (source: session 4c8d66b3, confidence: high)
