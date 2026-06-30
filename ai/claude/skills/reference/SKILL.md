---
name: reference
description: "Show a reference card of all workbench skills, agents, and reuse modes. TRIGGER when: user asks what skills/commands/agents are available, wants a quick reference, or asks how to use the workbench. SKIP: detailed help on a specific skill (invoke that skill directly)."
source: otto-workbench/ai/claude/skills/reference/SKILL.md
invocation: "/reference"
trigger: "what skills are available, show commands, help, reference card, what can you do"
skip: "Detailed help on a specific skill — invoke that skill directly"
output: "formatted reference card to stdout"
---

# Reference

Show a quick reference card of all available workbench skills, agents, and reuse modes.

---

## Steps

1. Run the reference script:
```bash
workbench-reference
```

2. Present the output directly — it is already formatted as a markdown table.
