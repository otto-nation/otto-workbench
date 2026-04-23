---
name: analyze-project
description: "Analyze a project's codebase and populate scaffolded .claude/CLAUDE.md and .claude/rules/ files with project-specific conventions. Run after scaffolding a new project."
source: otto-workbench/ai/claude/skills/analyze-project/SKILL.md
---

# Analyze Project

Reads the codebase and proposes content for scaffolded `.claude/CLAUDE.md` and `.claude/rules/` files.
Run with `/analyze-project` after `otto-workbench claude` scaffolds a new project.

---

## How It Works

Three sequential phases. Execute in order. Do not skip phases.

```
DISCOVER --> PROPOSE --> APPLY
```

---

## Phase 1: DISCOVER

**Goal:** Understand the project structure, conventions, and patterns.

### 1a. Read existing scaffold

```bash
cat .claude/CLAUDE.md
ls .claude/rules/
```

For each rule file in `.claude/rules/`, read it and note which sections are empty.

### 1b. Scan the codebase

Use Glob and Grep to understand:

**Project identity:**
- README, package manifests (`go.mod`, `package.json`, `build.gradle.kts`, etc.)
- What the project does, its purpose, who uses it

**Key paths:**
- Source directories (where production code lives)
- Test directories and test helpers
- Configuration files
- Generated code (and how it's generated — look for Makefiles, taskfiles, generate scripts)
- Migration files, schema definitions

**Dev workflow:**
- Build system (Makefile, Taskfile, mise, gradle, npm scripts)
- Available commands — read the task runner config to find build/test/lint/run commands
- CI configuration (`.github/workflows/`, `.gitlab-ci.yml`, etc.)

**Code patterns:**
- Error handling patterns (custom error types, wrapping conventions)
- Dependency injection or service wiring patterns
- Database access patterns (ORM, raw SQL, repository pattern)
- Testing patterns (frameworks, helpers, fixtures, factories)
- Import organization conventions

**Existing conventions:**
- Linter configs (`.eslintrc`, `.golangci.yml`, etc.) — these encode existing rules
- `.editorconfig`, `prettier`, formatting configs
- CLAUDE.md at the project root (distinct from `.claude/CLAUDE.md`)

### Output

A structured list of findings organized by target file:
- What belongs in CLAUDE.md (description, workflow commands, key paths, notes)
- What belongs in each rule file (conventions, testing patterns, language-specific rules)

---

## Phase 2: PROPOSE

**Goal:** Present proposed content for each file. Get user confirmation before writing.

For each file that needs content, present a proposal:

```
## .claude/CLAUDE.md

### Description (proposed)
> <1-2 sentence project description>

### Dev workflow (proposed)
> - Build: `<command>`
> - Test:  `<command>`
> - Lint:  `<command>`

### Key paths (proposed)
> - Source:    <path>
> - Tests:     <path>
> - Config:    <path>
> - Generated: <path> (do not edit)

### Notes (proposed)
> - <architectural constraint or dependency>
> - <anything that would burn someone unfamiliar>
```

```
## .claude/rules/conventions.md (proposed)
> - <convention derived from codebase analysis>
> - <convention derived from linter config>
```

```
## .claude/rules/testing.md (proposed)
> - <testing pattern observed>
```

```
## .claude/rules/<language>.md (proposed)
> - <language-specific convention>
```

### Rules for proposals

- **Only propose what you observed.** Do not invent conventions — derive them from the code, configs, and existing documentation.
- **Be specific.** "Use `RunTx` for database writes" is useful. "Follow best practices" is not.
- **Cite evidence.** For each proposed convention, note where you observed it (file, pattern).
- **Skip empty sections.** If you found nothing for a file, say so and move on. Do not fill sections with generic advice.
- **Respect existing content.** If a section already has content, propose additions only — never replace what's there.

Present all proposals, then ask the user which to apply. The user may accept all, reject some, or edit before applying.

---

## Phase 3: APPLY

**Goal:** Write confirmed content to the scaffolded files.

For each confirmed proposal:
1. Edit the target file, inserting content under the appropriate section header
2. Preserve existing content — append, don't overwrite

After all writes:
1. Print a summary of what was written and where
2. Suggest the user review the files and adjust as needed

---

## When to use

- After `otto-workbench claude` scaffolds a new project
- After `otto-workbench claude --force` re-scaffolds an existing project
- When `.claude/CLAUDE.md` or `.claude/rules/` files have empty sections

## Output location

- `.claude/CLAUDE.md` — project description, workflow, key paths, notes
- `.claude/rules/*.md` — project-specific conventions
