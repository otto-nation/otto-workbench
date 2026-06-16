# General Coding Principles

## Communication & Workflow

- For decisions with meaningful trade-offs, present alternatives with a recommendation and wait for approval
- Always ask for confirmation before architectural decisions or significant changes
- Never say "You're absolutely right"
- Before starting implementation work, verify: target branch/worktree, what's in scope, and desired depth of investigation. If the user's prompt is ambiguous on any of these, ask before acting — a one-line clarification prevents a 20-minute redirect
- When the user references a broken script, tool, or automation, fix the script's code — don't manually perform the action the script was supposed to automate
- Implement the practical fix before deep-diving into upstream or third-party source code. Ask before spending time on root-cause analysis in code you don't own

## Planning

- Before making any code changes, file edits, or multi-step modifications: present
  a plan with options, pros/cons, and a recommendation — then explicitly wait for
  approval. Never proceed to implementation until the user has chosen an option.
- Plans describe *what* changes and *why* — keep implementation details minimal.
  Specifics come from reading the actual codebase during implementation.
- Each phase of a plan must be independently committable and leave the codebase
  in a shippable state. If a phase is too large for one commit, split it.
- After a plan is approved, verify every API, method signature, and constant
  referenced before writing implementation code. Plans are written before the
  codebase is read — their specifics will often be wrong.
- Save all plan documents (including superpowers plans) to `ignore/plans/`
- Save all spec/design documents (including superpowers specs) to `ignore/specs/`

## Code Quality

- Prefer solutions that work for the general case, not just the immediate scenario — check if the pattern exists elsewhere before writing a narrow fix
- Always check existing tooling before adding anything new
- Never introduce changes that violate SSOT or DRY — if data or logic already has a single owner, reference it instead of duplicating. Before adding a constant, config value, or pattern, check if it already exists elsewhere. Changes that create a second source of truth introduce drift and must be reworked
- Never defer review findings to issues — fix them in the current PR or create separate PRs. Do not suggest "track separately" as the response to review findings
- When automation fails partway through, make it idempotent and re-runnable rather than adding checkpoint/retry/resume logic — simpler tools are easier to reason about and maintain

## Debugging

- Always fix root causes, not symptoms — if a process hits a resource limit, investigate why it consumed so much, don't just raise the limit
- When diagnosing an issue, check if the diagnostic path itself was sufficient. If you had to manually reconstruct data that should have been persisted, add instrumentation as part of the fix
- Persist diagnostic data that would help future investigations — structured files (JSON) over transient console output, surviving successful runs not just failures

## Unknowns & Assumptions

- Do not make assumptions about code without context
- If there are unknowns, surface them before writing code
- When suggesting third-party libraries, prioritize maintained, non-deprecated, recently updated ones

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
- When a foundational method's contract changes, audit every test that asserts
  the old behavior and update it before declaring the work done
- Prefer real dependencies over mocks when feasible — mocks hide integration bugs
- Every bug fix and behavioral change must include a new or updated test that
  would have caught the issue. No fix is complete without a regression test

