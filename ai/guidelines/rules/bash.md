---
paths:
  - "**/*.sh"
---

# Bash / Shell

## Code Style
- Always quote variables: `"$VAR"` not `$VAR`
- Use `[[` instead of `[` for conditionals
- Use meaningful argument names — not `$1`, `$2`
- Add error handling with `set -e` or explicit checks

## Best Practices
- Add usage/help documentation
- Validate required arguments
- Use functions for reusable logic
