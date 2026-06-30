---
paths:
  - "**/SKILL.md"
  - "ai/claude/skills/**"
  - "ai/claude/bin/**"
---

# Claude Skills — Authoring

## Authoring

- When adding a skill, agent, or task: update source frontmatter (SKILL.md, agent .md) and run `generate-tool-context` — generated sections in ai-automation.md, tools.md, and components.md update automatically
- When adding or changing auto-triggered lifecycle behavior (hooks, cooldowns, pending flags), update both the `should-*.sh` script constants and the skill's SKILL.md frontmatter (`lifecycle_*` fields), then run `generate-tool-context`

## Code Blocks

- Never use `${var//pattern/replacement}` or `${var#pattern}` in SKILL.md code blocks — Claude Code's static analyzer can't parse these and triggers a permission prompt every time. Use piped alternatives instead:
  - `echo "$var" | tr '/' '-'` instead of `${var//\//-}`
  - `echo "$var" | sed 's/pattern/replacement/g'` for complex substitutions
  - This only applies to code blocks in SKILL.md files (run via Bash tool). Standalone `.sh` scripts run directly and are unaffected
