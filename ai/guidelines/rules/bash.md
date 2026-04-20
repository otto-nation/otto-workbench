---
paths:
  - "**/*.sh"
---

# Bash / Shell

## Code Style
- Use meaningful argument names — not `$1`, `$2`

## Best Practices
- Scripts in `bin/` should include usage documentation
- Validate required arguments
- Use functions for reusable logic
- Under `set -e`, commands that return non-zero on no-match (grep, find, diff) must be guarded with `|| true` or wrapped in `if`/`while` — unguarded usage causes silent script exits
