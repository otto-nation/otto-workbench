# AI Coding Assistant Guidelines - Language-Specific

Language-specific coding standards for AI coding assistants (Kiro CLI, Cursor, GitHub Copilot, Claude, ChatGPT, etc.).

## Installation

### Kiro CLI
Save to `~/.kiro/steering/language-specific.md`

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

## Kotlin

### Testing
- `@BeforeAll` must be in a companion object at the top of the class, also annotated with `@JvmStatic`
- Use `shouldNotBeNull()` instead of `!!`
- Use Kotest assertions and matchers
- Use MockK instead of Mockito

### Code Style
- Always use named arguments for function calls

---

## Java

### Code Style
- Follow standard Java conventions
- Use descriptive variable names
- Prefer composition over inheritance

---

## Go

### Modern Syntax
- Don't use `m[k]=v` loop - use `maps.Copy`
- Don't use `interface{}` - use `any`

### Code Style
- Follow standard Go formatting (gofmt)
- Use meaningful package names

---

## TypeScript / JavaScript

### Code Style
- Use `const` by default, `let` when reassignment needed
- Never use `var`
- Prefer arrow functions for callbacks
- Use async/await over raw Promises

### Testing
- Use descriptive test names
- Follow AAA pattern (Arrange, Act, Assert)

---

## Python

### Code Style
- Follow PEP 8 style guide
- Use type hints for function signatures
- Prefer f-strings for string formatting

### Testing
- Use pytest for testing
- Use fixtures for test setup

---

## Bash / Shell

### Code Style
- Always quote variables: `"$VAR"` not `$VAR`
- Use `[[` instead of `[` for conditionals
- Add error handling with `set -e` or explicit checks
- Use functions for reusable logic

### Best Practices
- Add usage/help documentation
- Validate required arguments
- Use meaningful variable names (not `$1`, `$2`)

---

## Docker

### Compose Files
- The `version` field is deprecated - never include it
- Do not use `docker-compose` command - use `docker compose` (v2)

### Dockerfiles
- Use multi-stage builds when appropriate
- Minimize layers
- Use specific base image tags (not `latest`)
- Run as non-root user when possible

---

## YAML

### Code Style
- Use 2 spaces for indentation
- Quote strings when they contain special characters
- Use explicit `true`/`false` instead of `yes`/`no`

---

## SQL

### Code Style
- Use uppercase for SQL keywords
- Use meaningful table and column aliases
- Indent subqueries properly

### Best Practices
- Always use parameterized queries (prevent SQL injection)
- Add appropriate indexes
- Use transactions when modifying multiple tables
