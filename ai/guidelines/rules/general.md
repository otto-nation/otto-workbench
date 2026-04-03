# General Coding Principles

## Communication & Workflow

- Give alternatives when applicable with pros and cons for each option
- Present the recommended option first with reasoning
- Always ask which option to proceed with before making changes
- Provide brief summaries of changes made
- For large features, break into smaller incremental changes
- Always ask for confirmation before architectural decisions or significant changes
- Never say "You're absolutely right"

## Planning

- Before making any code changes, file edits, or multi-step modifications: present
  a plan with options, pros/cons, and a recommendation — then explicitly wait for
  approval. Never proceed to implementation until the user has chosen an option.
- Plans describe *what* changes and *why*. They do not contain code snippets,
  pseudo-code, or implementation details — those come from reading the actual
  codebase during implementation.
- Each phase of a plan must be independently committable and leave the codebase
  in a shippable state. If a phase is too large for one commit, split it.
- After a plan is approved, verify every API, method signature, and constant
  referenced before writing implementation code. Plans are written before the
  codebase is read — their specifics will often be wrong.

## Code Quality

- Prioritize clean, reusable, generic, extensible, and maintainable production-ready code
- Design for multiple use cases, not just the immediate issue
- Never make special-case or hardcoded changes for a single scenario
- Never suggest changes with empty functions
- Follow DRY (Don't Repeat Yourself) and KISS (Keep It Simple, Stupid) principles
- Use guard clauses and early returns to reduce nesting
- Always check existing tooling before adding anything new

## Unknowns & Assumptions

- Do not make assumptions about code without context
- If there are unknowns, surface them before writing code
- When suggesting third-party libraries, prioritize maintained, non-deprecated, recently updated ones

## Code Style

### Imports
- Never use wildcard imports
- Always sort imports

### Constants & Magic Values
- Never use magic values — always give them context
- Prioritize constants, enums, or variables with descriptive names
- Durations should always be named constants (e.g. `AWAIT_AT_MOST = Duration.ofSeconds(30)`)
- Check if constants already exist before creating new ones

### Comments & Documentation
- Comments should be production-ready; place them above the line, never inline
- Do not add comments that exist only to explain what a prompt change did
- Silent fallbacks and defense-in-depth patterns require a comment explaining intent
- Prioritize adding to existing documentation rather than creating new docs

## Testing

- Write tests the same way as existing tests in the project
- Tests are not complete until they run and all pass
- Never disable a test as a fix for a failing test
- Do not add tests that simply assert constant values
- When a foundational method's contract changes, audit every test that asserts
  the old behavior and update it before declaring the work done
- Avoid mocking when possible

## Git

- Never use `--force` or `--force-with-lease` on git push. Always try a regular push first. If it fails because the branch diverged, tell the user and let them decide how to proceed
- Never commit unless explicitly asked

## Incremental Changes

- Don't make sweeping changes — do things incrementally
- Make changes in small, independently shippable steps
