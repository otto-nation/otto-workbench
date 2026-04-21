# Review Checklist Template

Review checklists codify domain knowledge for the reviewer agent. They live in
`.claude/review/` and are loaded during Phase 0 (Discovery) based on which files
appear in the diff.

## Format

Each checklist is a markdown file with YAML frontmatter for path-scoping:

```markdown
---
paths:
  - "pkg/**/*.go"
  - "internal/service/**"
---

# <Domain Name>

## Lookup Table

| I need to... | Use this | Not this |
|---|---|---|
| <task> | `<correct utility>` | `<common mistake>` |

## Anti-Patterns

### Don't: <title>
BAD: `<incorrect code>`
GOOD: `<correct code>`
Why: <rationale>

## Decision Tree

- <decision point>?
  - Yes --> <path A>
  - No --> <path B>

## Reference Implementations

- <description>: `<file>:<lines>`
```

## Guidelines

- `paths:` frontmatter uses the same glob syntax as `.claude/rules/` files
- Only checklists whose paths match files in the diff are loaded
- Keep checklists focused on one domain (database, auth, events, etc.)
- Use `*.local.md` for personal/experimental checklists (gitignored)
- Anti-patterns should show both BAD and GOOD with a Why line
- Reference implementations point to canonical examples in the codebase
- Lookup tables answer "I need to... use this" for common operations
