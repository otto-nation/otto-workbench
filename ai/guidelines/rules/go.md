---
paths:
  - "**/*.go"
---

# Go

## Imports
- Separate import groups with a blank line: stdlib, then third-party, then internal — `goimports` enforces this and will reformat on the next pass

## Error Handling
- Bare map lookups on external data (DB rows, API responses, user input) MUST use the comma-ok idiom and handle the missing-key case — a missing key silently returns the zero value, which for proto enums means `UNSPECIFIED`

## Pagination
- Use keyset cursor-based pagination via `lib-go/pkg/pagination` (`DecodeTimeIDCursor` / `EncodeTimeIDCursor`) — offset-based pagination (`LIMIT/OFFSET`) is not the codebase standard and degrades on large tables

## Temporal
- Never rename a registered Temporal activity or workflow type — the string-level ActivityType is recorded in workflow history and renaming breaks replay determinism for in-flight executions. Use `workflow.GetVersion()` to branch between old and new names during transitions, or parameterize behavior instead of renaming

## Modern Syntax
- Use `maps.Copy` instead of a map assignment loop
