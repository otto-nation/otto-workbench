# User Overrides

The `user/` directory lets you customize AI config — rules, skills, agents, guidelines, and settings — without editing tracked files. It is gitignored and layered on top of base config during sync.

## Override Types

| Component | Base | User | Mechanism |
|-----------|------|------|-----------|
| Rules | `ai/guidelines/rules/*.md` | `user/ai/guidelines/rules/*.md` | Same-name file replaces base |
| Skills | `ai/claude/skills/<name>/` | `user/ai/claude/skills/<name>/` | Same-name directory replaces base |
| Agents | `ai/claude/agents/*.md` | `user/ai/claude/agents/*.md` | Same-name file replaces base |
| Guidelines | `ai/claude/CLAUDE.md` | `user/ai/claude/CLAUDE.md` | Full replacement |
| Guidelines | `ai/claude/CLAUDE.md` | `user/ai/claude/CLAUDE.local.md` | Appended after base |
| Settings | `ai/claude/settings.json` | `user/ai/claude/settings.json` | Deep JSON merge (user wins) |

For guidelines, replacement (`CLAUDE.md`) takes precedence over append (`CLAUDE.local.md`). Use append mode to add machine-specific instructions without losing base content.

## Disabling Items

Create a `.disabled` sentinel in the user directory to suppress a base item entirely:

```
user/ai/claude/skills/some-skill.disabled   # suppresses ai/claude/skills/some-skill/
user/ai/claude/agents/reviewer.disabled      # suppresses ai/claude/agents/reviewer.md
user/ai/guidelines/rules/security.disabled   # suppresses ai/guidelines/rules/security.md
```

The sentinel is an empty file — only the name matters.

## Directory Structure

```
user/
└── ai/
    ├── claude/
    │   ├── CLAUDE.md              # full guidelines replacement
    │   ├── CLAUDE.local.md        # guidelines append
    │   ├── settings.json          # settings merge
    │   ├── agents/
    │   │   ├── debugger.md        # replaces base debugger agent
    │   │   └── reviewer.disabled  # suppresses base reviewer agent
    │   └── skills/
    │       └── my-skill/          # adds a new skill
    │           └── skill.md
    └── guidelines/
        └── rules/
            ├── my-rule.md         # adds a new rule
            └── security.disabled  # suppresses base security rule
```

## When Overrides Apply

Overrides are resolved during `otto-workbench sync` and `otto-workbench claude`. Active overrides (replacements, additions, disables) are printed in the sync summary.

## Implementation

- `resolve_layers()` in [`lib/files.sh`](../lib/files.sh) merges base and user directories by basename
- `is_disabled()` in [`lib/files.sh`](../lib/files.sh) checks for `.disabled` sentinels
- Constants (`USER_AI_DIR`, `USER_CLAUDE_DIR`, etc.) in [`lib/constants.sh`](../lib/constants.sh)
- Step functions in [`ai/claude/steps.sh`](../ai/claude/steps.sh) consume the merged results

## Examples

Replace the debugger agent with a custom version:

```bash
cp ai/claude/agents/debugger.md user/ai/claude/agents/debugger.md
# edit user/ai/claude/agents/debugger.md, then run otto-workbench sync
```

Disable a skill you don't use:

```bash
touch user/ai/claude/skills/some-skill.disabled
otto-workbench sync
```

Add machine-specific guidelines without replacing the base:

```bash
echo "- Always use verbose output on this machine" > user/ai/claude/CLAUDE.local.md
otto-workbench sync
```
