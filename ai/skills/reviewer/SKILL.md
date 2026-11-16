---
name: reviewer
description: "Structured code review for PRs and diffs — produces categorized findings (must-fix, should-fix, nit). TRIGGER when: a review agent is dispatched against a PR, a diff, or a working tree."
source: otto-workbench/ai/skills/reviewer/SKILL.md
agent: reviewer
trigger: "Loaded automatically for review agents. Pi has no agent files, so the protocol arrives as a skill."
---

<!-- Installed to Pi's discovery root only. Claude Code loads the same protocol
     from ~/.claude/agents/reviewer.md, so a skill copy there would be a second
     full transcript of it in the skill index. ai/skills/steps.sh splices the
     agent body in below at install time. -->

<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced at install with the body of ai/claude/agents/reviewer.md -->
