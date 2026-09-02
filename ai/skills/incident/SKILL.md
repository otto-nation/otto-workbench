---
name: incident
description: "Structured production incident triage — gathers symptoms, checks recent changes, forms ranked hypotheses, and modifies nothing. TRIGGER when: a production incident or outage needs triage. Read-only investigation."
source: otto-workbench/ai/skills/incident/SKILL.md
agent: incident
trigger: "Loaded automatically for incident triage. Pi has no agent files, so the protocol arrives as a skill."
---

<!-- Installed to Pi's discovery root only. Claude Code loads the same protocol
     from ~/.claude/agents/incident.md, so a skill copy there would be a second
     full transcript of it in the skill index. ai/skills/steps.sh splices the
     agent body in below at install time. -->

<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced at install with the body of ai/claude/agents/incident.md -->
