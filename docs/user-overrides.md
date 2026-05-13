# User Overrides

User overrides let you customize AI config — rules, skills, agents, guidelines, and settings — without editing tracked files. They live in `~/.config/workbench/overrides/ai/` and are layered on top of base config during sync.

This location is the same regardless of how you installed the workbench (git clone or Homebrew). It respects `XDG_CONFIG_HOME` if set.

## Override Types

| Component | Base | User | Mechanism |
|-----------|------|------|-----------|
| Rules | `ai/guidelines/rules/*.md` | `overrides/ai/guidelines/rules/*.md` | Same-name file replaces base |
| Skills | `ai/claude/skills/<name>/` | `overrides/ai/claude/skills/<name>/` | Same-name directory replaces base |
| Agents | `ai/claude/agents/*.md` | `overrides/ai/claude/agents/*.md` | Same-name file replaces base |
| Guidelines | `ai/claude/CLAUDE.md` | `overrides/ai/claude/CLAUDE.md` | Full replacement |
| Guidelines | `ai/claude/CLAUDE.md` | `overrides/ai/claude/CLAUDE.local.md` | Appended after base |
| Settings | `ai/claude/settings.json` | `overrides/ai/claude/settings.json` | Deep JSON merge (user wins) |

For guidelines, replacement (`CLAUDE.md`) takes precedence over append (`CLAUDE.local.md`). Use append mode to add machine-specific instructions without losing base content.

## Disabling Items

Create a `.disabled` sentinel in the user directory to suppress a base item entirely:

```
~/.config/workbench/overrides/ai/claude/skills/some-skill.disabled
~/.config/workbench/overrides/ai/claude/agents/reviewer.disabled
~/.config/workbench/overrides/ai/guidelines/rules/security.disabled
```

The sentinel is an empty file — only the name matters.

## Directory Structure

```
~/.config/workbench/overrides/
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

## CLI Management

```bash
otto-workbench override list                      # list active overrides
otto-workbench override add agent debugger         # copy default for editing
otto-workbench override disable skill some-skill   # suppress a default
otto-workbench override enable skill some-skill    # re-enable a disabled default
otto-workbench override status                     # show overrides vs defaults
```

## Implementation

- `resolve_layers()` in [`lib/files.sh`](../lib/files.sh) merges base and user directories by basename
- `is_disabled()` in [`lib/files.sh`](../lib/files.sh) checks for `.disabled` sentinels
- Constants (`USER_AI_DIR`, `USER_CLAUDE_DIR`, etc.) in [`lib/constants.sh`](../lib/constants.sh)
- Step functions in [`ai/claude/steps.sh`](../ai/claude/steps.sh) consume the merged results

## Examples

Replace the debugger agent with a custom version:

```bash
otto-workbench override add agent debugger
# edit ~/.config/workbench/overrides/ai/claude/agents/debugger.md
otto-workbench sync
```

Disable a skill you don't use:

```bash
otto-workbench override disable skill some-skill
otto-workbench sync
```

Add machine-specific guidelines without replacing the base:

```bash
echo "- Always use verbose output on this machine" > ~/.config/workbench/overrides/ai/claude/CLAUDE.local.md
otto-workbench sync
```
