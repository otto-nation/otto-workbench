# Workbench guidelines

The sections below are this machine's global coding guidelines. They are shared
with every other agent harness here, and are reproduced from the same source.

## Agent protocols

Four situations have a defined protocol, each packaged as a skill in
`~/.agents/skills/`. Invoke the matching skill before taking action, not after:

| Situation | Skill | Constraint |
|-----------|-------|------------|
| Investigating a bug, test failure, or unexpected behavior | `debugger` | Diagnose before fixing |
| Production incident or outage triage | `incident` | Read-only investigation |
| Dependency upgrade or framework migration | `migrate` | Plan before changing |
| Code review (PR or diff) | `reviewer` | Review before approving |
