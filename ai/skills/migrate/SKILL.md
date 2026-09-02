---
name: migrate
description: "Analyze a codebase for migration work and produce a phased upgrade plan, without applying changes. TRIGGER when: a dependency upgrade or framework migration is being planned. Plan before changing."
source: otto-workbench/ai/skills/migrate/SKILL.md
agent: migrate
trigger: "Loaded automatically for migration planning. Pi has no agent files, so the protocol arrives as a skill."
---

<!-- Installed to Pi's discovery root only. Claude Code loads the same protocol
     from ~/.claude/agents/migrate.md, so a skill copy there would be a second
     full transcript of it in the skill index. ai/skills/steps.sh splices the
     agent body in below at install time. -->

<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced at install with the body of ai/claude/agents/migrate.md -->
