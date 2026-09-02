---
paths:
  - "**/SKILL.md"
  - "ai/skills/**"
  - "ai/claude/bin/**"
---

# Skills — Authoring

## Authoring

- When adding a skill, agent, or task: update source frontmatter (SKILL.md, agent .md), then run `generate-tool-context` for the AI rule files and `compose-docs` for the generated sections of ai-automation.md, tools.md, and components.md
- When adding or changing auto-triggered lifecycle behavior (hooks, cooldowns, pending flags), update both the `should-*.sh` script constants and the skill's SKILL.md frontmatter (`lifecycle_*` fields), then run `generate-tool-context` and `compose-docs`
- Never edit a `docs/*.md` that carries a "Generated from … by bin/local/compose-docs" banner — edit its `docs/*.src.md`, or the source data behind the include directive
- `agent: <name>` — optional. Declares that the skill's body is an agent protocol maintained in `ai/claude/agents/<name>.md`. Such a skill installs to Pi's discovery root only, with that agent's body spliced in place of the `<!-- AGENT_PROTOCOL_PLACEHOLDER: -->` comment; Claude Code loads the same protocol as an agent and must not carry a second copy as a skill. A skill declaring `agent:` does not need `invocation:`.
  Every agent that is matched to a *situation* rather than dispatched by code
  needs such a stub, or its protocol reaches Claude Code and not Pi —
  `bin/local/validate-skills` fails on an agent file with neither a stub nor an
  entry in its `PROGRAMMATIC_AGENTS` list.

## Code Blocks

- Never use `${var//pattern/replacement}` or `${var#pattern}` in SKILL.md code blocks — Claude Code's static analyzer can't parse these and triggers a permission prompt every time. Use piped alternatives instead:
  - `echo "$var" | tr '/' '-'` instead of `${var//\//-}`
  - `echo "$var" | sed 's/pattern/replacement/g'` for complex substitutions
  - This only applies to code blocks in SKILL.md files (run via Bash tool). Standalone `.sh` scripts run directly and are unaffected
