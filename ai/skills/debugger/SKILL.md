---
name: debugger
description: "Systematic code-level bug diagnosis — traces through source code to find root causes, and modifies nothing. TRIGGER when: investigating a bug, a test failure, or unexpected behavior. Diagnose before fixing."
source: otto-workbench/ai/skills/debugger/SKILL.md
agent: debugger
trigger: "Loaded automatically for debugging work. Pi has no agent files, so the protocol arrives as a skill."
---

<!-- Installed to Pi's discovery root only. Claude Code loads the same protocol
     from ~/.claude/agents/debugger.md, so a skill copy there would be a second
     full transcript of it in the skill index. ai/skills/steps.sh splices the
     agent body in below at install time. -->

<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced at install with the body of ai/claude/agents/debugger.md -->
