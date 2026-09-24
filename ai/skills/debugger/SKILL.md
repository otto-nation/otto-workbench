---
name: debugger
description: "Systematic code-level bug diagnosis — traces through source code to find root causes, and modifies nothing. TRIGGER when: a bug, a test failure, or unexpected behavior needs a diagnosis before anyone changes code, or an investigation is dispatched read-only. SKIP: this session will implement the fix — that is superpowers:systematic-debugging, whose last phase implements; this protocol stops at the diagnosis."
source: otto-workbench/ai/skills/debugger/SKILL.md
agent: debugger
trigger: "Loaded automatically for debugging work. Pi has no agent files, so the protocol arrives as a skill."
skip: "Do not use when this session will implement the fix — superpowers:systematic-debugging owns that path and its fourth phase is the implementation. This protocol ends at a diagnosis and modifies nothing."
---

<!-- Installed to Pi's discovery root only. Claude Code loads the same protocol
     from ~/.claude/agents/debugger.md, so a skill copy there would be a second
     full transcript of it in the skill index. ai/skills/steps.sh splices the
     agent body in below at install time — including that file's note on the
     diagnose-vs-implement boundary this description also states, which is why
     the note is not repeated here. -->
<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced at install with the body of ai/claude/agents/debugger.md -->
