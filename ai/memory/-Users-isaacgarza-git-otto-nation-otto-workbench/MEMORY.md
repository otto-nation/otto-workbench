# Workbench Memory Index

## Topic Files

- [architecture.md](architecture.md) — Current file layout, component map, lib modules, script reorganization (updated 2026-04-19)
- [decisions.md](decisions.md) — User preferences, recorded decisions, known sync warnings
- [audit.md](audit.md) — March 2026 comprehensive audit: 58 findings (R1–R15, E1–E11, M1–M23) — all addressed
- [plan_audit_fixes.md](plan_audit_fixes.md) — 5-phase fix plan for March 2026 audit — Phases 1–5 complete
- [plan_audit_fixes_p2.md](plan_audit_fixes_p2.md) — 4-phase plan for 23 second-pass findings (NEW-1–NEW-23) — Phases A–D complete

## Quick Reference

**Repo:** `/Users/isaacgarza/git/otto-nation/otto-workbench`

**Audit status:** All 81 findings resolved (58 original + 23 second pass). No open findings.

**Recent structural changes (PR #36, 2026-04-17):**
- Scripts split into per-component `bin/` dirs (`ai/bin/`, `git/bin/`, `docker/bin/`, `zsh/bin/`, `terminals/bin/`)
- `ai/kiro/`, `bin/claude-init`, `terminals/iterm/` removed
- `lib/worktree.sh` added; `lib/` split into focused modules
- `bin/template` added as canonical new-script scaffold (with `-h`/`--help`)

**Key conventions:**
- All new scripts: use `bin/template`, implement `-h`/`--help`
- All paths: come from `lib/constants.sh`, no inline `$HOME/`
- Idempotency required on all sync functions and migrations
- Return values via `local -n` nameref, never `printf -v`
