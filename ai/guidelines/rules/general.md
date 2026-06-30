# General Coding Principles

## Workflow

- For decisions with meaningful trade-offs, present alternatives with a recommendation and wait for approval
- Always ask for confirmation before architectural decisions or significant changes
- When the user references a broken script, tool, or automation, fix the script's code — don't manually perform the action the script was supposed to automate
- Implement the practical fix before deep-diving into upstream or third-party source code. Ask before spending time on root-cause analysis in code you don't own

## Planning

Before code changes, walk this ladder — stop at the first unmet gate:

1. **Scope clear?** — target branch/worktree, what's in scope, and desired depth must all be unambiguous. If the user's prompt is ambiguous on any of these, ask before acting
2. **Plan needed?** — for multi-step modifications, present options with pros/cons and a recommendation. Wait for approval. Never proceed without it
3. **Plan verified?** — verify every API, method signature, and constant referenced against the actual codebase. Plans are written before reading code — specifics will be wrong
4. **Build** — each phase independently committable, shippable state. If too large for one commit, split it

Plans describe *what* and *why* — not implementation details.
Save plans to `ignore/plans/`, specs to `ignore/specs/`.

## Code Quality

Reuse ladder — stop at the first rung that solves the problem:

1. **Already in this codebase?** Reuse it — check if the pattern exists elsewhere before writing a narrow fix
2. **Stdlib / language built-in?** Use it
3. **Already-installed dependency?** Use it
4. **One line?** Write it inline — don't extract a function or file for what fits in one line
5. **New utility function?** Write the minimum
6. **New dependency?** Justify it

- Never introduce changes that violate SSOT or DRY — if data or logic already has a single owner, reference it instead of duplicating. Before adding a constant, config value, or pattern, check if it already exists elsewhere
- Never defer review findings to issues — fix them in the current PR or create separate PRs
- When automation fails partway through, make it idempotent and re-runnable rather than adding checkpoint/retry/resume logic

## Debugging

On failure, diagnose in this order — do NOT retry with variations:

1. **Root cause** — investigate why, not just what. Resource limit hit? Find the consumer, don't raise the limit
2. **Diagnostics** — was the diagnostic path sufficient? If you manually reconstructed data that should have been persisted, add instrumentation as part of the fix
3. **Persist** — structured files (JSON) over transient console output; surviving successful runs, not just failures

## Code Style

### Comments & Documentation
- Comments should be production-ready; place them above the line, never inline
- Do not add comments that exist only to explain what a prompt change did
- Silent fallbacks and defense-in-depth patterns require a comment explaining intent
- When adding docs, extend existing files rather than creating new ones
- When adding CLI commands or changing command signatures, update `docs/ai-automation.md` and/or `README.md`

## Testing

- Write tests the same way as existing tests in the project
- Tests are not complete until they run and all pass
- Never disable a test as a fix for a failing test
- Do not add tests that simply assert constant values
- When a foundational method's contract changes, audit every test that asserts the old behavior and update it
- Prefer real dependencies over mocks when feasible — mocks hide integration bugs
- Every bug fix and behavioral change must include a regression test
