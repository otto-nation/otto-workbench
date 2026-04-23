# Workflow Tools
<!-- AUTO-GENERATED — do not edit directly -->
<!-- Regenerate: generate-tool-context -->

## Workbench Scripts

### task
AI-powered Git automation runner; wraps go-task with global/local Taskfile routing
- **When to use**: Running any dev workflow — commits, PRs, brew management
- **Usage**: `task commit  |  task pr:create  |  task --global <cmd>`
- **Docs**: https://taskfile.dev

### otto-workbench
Manage your workbench developer environment
- **When to use**: After pulling workbench updates or when config gets out of sync
- **Usage**: `otto-workbench sync  |  otto-workbench claude [--force]  |  otto-workbench changelog`

### mem-analyze
macOS memory analysis report — pressure, swap usage, top processes, per-user totals
- **When to use**: Diagnosing memory issues, high swap, or identifying memory-hungry processes
- **Usage**: `mem-analyze`

### get-secret
Interactively retrieves a secret from AWS Secrets Manager by listing and selecting
- **When to use**: Fetching credentials or config values stored in Secrets Manager
- **Usage**: `get-secret`

### validate-registries
Validates all tool registry YAML files for schema correctness and cross-file consistency
- **When to use**: After adding or editing any registry.yml; runs automatically on pre-push
- **Usage**: `bin/validate-registries`

### validate-components
Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency
- **When to use**: After adding or modifying any component (steps.sh, setup.conf, install.components)
- **Usage**: `bin/validate-components`

### validate-migrations
Validates migration file naming, function naming, and shebang conventions
- **When to use**: After adding or editing any migration file; runs automatically on pre-push
- **Usage**: `bin/validate-migrations`

### generate-tool-context
Generates ai/guidelines/rules/tools.generated.md from the domain registries
- **When to use**: After adding/updating any registry.yml entry; runs automatically on pre-push and workbench sync
- **Usage**: `generate-tool-context`

## Claude Scripts

### claude-review
Run Claude's reviewer agent on a PR and optionally post findings to GitHub
- **When to use**: Running a structured code review on a PR with optional GitHub posting
- **Usage**: `claude-review <pr_url_or_number>  |  claude-review post <pr_number>`

### claude-rules
Manages local Claude Code rule additions not tracked in the workbench
- **When to use**: Adding machine-specific or project-specific Claude instructions
- **Usage**: `claude-rules add <domain> "rule"  |  claude-rules list  |  claude-rules status`

## Serena Scripts

### serena-mcp
Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence
- **When to use**: Enabling Serena LSP-based code intelligence in a specific project
- **Usage**: `serena-mcp init  |  serena-mcp status`
