# AI Coding Assistant Guidelines - General

Universal coding standards and best practices for AI coding assistants (Cursor, GitHub Copilot, Claude, ChatGPT, etc.).

## Installation

### Cursor
Add to `.cursorrules` or workspace settings

### GitHub Copilot
Add to repository `.github/copilot-instructions.md`

### Claude Code
Global rules: save to `~/.claude/CLAUDE.md`
Project-level rules: save to `CLAUDE.md` in the repository root

### Other AI Tools
Include in your project documentation or AI context

---

## Communication & Workflow

- Give alternatives when applicable with pros and cons for each option
- Present the recommended option first with reasoning
- Always ask which option to proceed with before making changes
- Provide brief summaries of changes made
- For large features, break into smaller incremental changes
- Always ask for confirmation before architectural decisions or significant changes
- Never say "You're absolutely right"

## Planning

- Plans describe *what* changes and *why*. They do not contain code snippets,
  pseudo-code, or implementation details — those come from reading the actual
  codebase during implementation.
- Each phase of a plan must be independently committable and leave the codebase
  in a shippable state. If a phase is too large for one commit, split it.
- After a plan is approved, verify every API, method signature, and constant
  referenced before writing implementation code. Plans are written before the
  codebase is read — their specifics will often be wrong.

## Code Quality Principles

- Prioritize clean, reusable, generic, extensible, and maintainable production-ready code
- Design for multiple use cases, not just the immediate issue
- Never make special-case or hardcoded changes for a single scenario
- Never suggest changes with empty functions
- Follow DRY (Don't Repeat Yourself) and KISS (Keep It Simple, Stupid) principles
- Use guard clauses and early returns to reduce nesting
- Always check existing tooling before adding anything new

## Unknowns & Assumptions

- Do not make assumptions about code without context
- If there is confusion or unknowns, reply with the unknowns only and nothing else
- When suggesting third-party libraries, prioritize maintained, non-deprecated, recently updated ones

## Code Style

### Imports
- Never use wildcard imports (e.g., `import java.util.*`)
- Always sort imports

### Constants & Magic Values
- Never use magic values - always give them context
- Prioritize constants, enums, or variables with descriptive names
- Use constants in strings when applicable
- Never repeat numerical values without constants for context
- Durations should always be constants with appropriate names (e.g., `AWAIT_AT_MOST = Duration.ofSeconds(30)`)
- Check if constants already exist before creating new ones

### Comments & Documentation
- Don't add comments just to explain prompt changes
- Comments should be production-ready
- Prioritize function-level comments
- Never add comments at the end of a line - always above
- Silent fallbacks and defense-in-depth patterns require a comment explaining intent — otherwise they read as bugs
- Add KDocs or JavaDocs similar to other classes in the project
- Prioritize adding to existing documentation rather than creating new docs

## Testing Guidelines

### General Testing
- Write tests the same way as existing tests in the project
- Tests are not complete until they run and all pass
- Never add `@Disabled` to a test as a fix for an issue
- Do not add tests that simply assert constant values
- When a foundational method's contract changes, audit every test that asserts the old behavior and update it before declaring the work done
- When writing tests, do not jump to adding new code without first asking for confirmation

### Test Structure
- Keep private functions at the top of the class after property declarations
- Companion objects should be after private functions
- Always name functions annotated with `@BeforeAll`, `@BeforeEach`, etc. as the camelCase name of the annotation

### Mocking & Dependencies
- Avoid mocking in integration tests and unit tests when possible
- Set mocks at field-level declaration when possible
- Avoid using `any()` when possible; specify the type if `any()` is required
- Use dependency injection constructors to set class properties in tests

### Integration Tests
- Integration tests should be suffixed with `IT` not `IntegrationTest`
- Do not add test containers that are not needed - keep things minimal

### Configuration Tests
- Write unit tests for configuration classes

### API Tests
- Use constants for API paths
- Look to other tests/APIs in repos for comparison and guidance

## Git & Version Control

- Never git force push without confirming
- Never commit unless explicitly asked

## Incremental Changes

- Don't make sweeping changes - do things incrementally
- Always ask if user wants to proceed with the next change
