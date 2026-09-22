---
name: reviewer-lite
description: "Lightweight code review from pre-collected data — produces categorized findings (must-fix, should-fix, nit) with no context gathering. TRIGGER when: a review agent is dispatched for a scout, group, or disprove phase, or for a low-effort review."
source: otto-workbench/ai/skills/reviewer-lite/SKILL.md
agent: reviewer-lite
trigger: "Loaded automatically for review agents. Pi has no agent files, so the protocol arrives as a skill."
---

<!-- Installed to Pi's discovery root only. Claude Code loads the same protocol
     from ~/.claude/agents/reviewer-lite.md, so a skill copy there would be a
     second full transcript of it in the skill index. ai/skills/steps.sh splices
     the agent body in below at install time. -->

<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced at install with the body of ai/claude/agents/reviewer-lite.md -->
