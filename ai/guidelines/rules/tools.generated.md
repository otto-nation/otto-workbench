# Tools
<!-- AUTO-GENERATED — do not edit directly -->
<!-- Regenerate: generate-tool-context -->

## Workbench Scripts

### task
AI-powered Git automation runner; wraps go-task with global/local Taskfile routing
- **When to use**: Running any dev workflow — commits, PRs, brew management
- **Usage**: `task commit  |  task pr:create  |  task --global <cmd>`

### otto-workbench
Manage your workbench developer environment
- **When to use**: After pulling workbench updates or when config gets out of sync
- **Usage**: `otto-workbench sync  |  otto-workbench ai init  |  otto-workbench ai sync  |  otto-workbench changelog`

### wt-init
Convert a regular git repo to a bare repo with worktrees
- **When to use**: Setting up a repo for parallel worktree development with worktrunk
- **Usage**: `wt-init  |  wt-init ~/code/myrepo  |  wt-init --dry-run`

### lint-sweep
Sweep lint violations across multiple Go repos — detect, report, and optionally create fix branches
- **When to use**: Remediating a lint rule across many repos at once (e.g. no-stdlib-log, no-raw-getenv)
- **Usage**: `lint-sweep --rule no-stdlib-log --repos 'svc-*'  |  lint-sweep --rule no-raw-getenv --repos ~/src/maximum/main/svc-* --fix  |  lint-sweep --rule <name> --repos . --dry-run`

### validate-nesting
Validate bash, Python, and Go script nesting depth to enforce flat control flow
- **When to use**: After writing deeply nested conditionals or loops; runs automatically on pre-push for all repos
- **Usage**: `validate-nesting [--quiet] [--max-depth N] [file...]`
- **mem-analyze** — macOS memory analysis report — pressure, swap usage, top processes, per-user totals
- **wt-cleanup** — Remove stale git worktrees — merged branches and optionally age-based cleanup
- **resolve-branch** — Resolve a fuzzy branch name to an exact git branch — tries exact, worktree, separator, fuzzy
- **gcloud-reauth** — Check GCP application-default credentials and re-login if expired, with self-managed launchd agent
- **get-secret** — Interactively retrieves a secret from AWS Secrets Manager by listing and selecting

## Brew Tools

### worktrunk
Git worktree manager — create, switch, list, merge, and remove worktrees with hooks and CI integration
- **When to use**: Managing parallel git worktrees for concurrent feature development or isolating Claude Code agents in separate worktrees
- **Usage**: `wt switch -c feat/auth  |  wt list  |  wt merge  |  wt remove feat/auth`
- **docker** — Docker CLI — build, run, and manage containers against any backend runtime
- **jq** — JSON processor for querying, filtering, and transforming JSON data
- **yq** — YAML/JSON/TOML processor — like jq but for YAML
- **gh** — GitHub CLI — manage PRs, issues, repos, checks, and releases from the terminal
- **shellcheck** — Static analysis tool for shell scripts — catches bugs and style issues
- **bats-core** — Bash Automated Testing System — unit testing framework for shell scripts
- **tree** — Recursive directory listing tool — visualizes folder structure as a tree
- **gitleaks** — Secret scanner — detects committed credentials, tokens, and keys

## Claude Scripts

### claude-review
Run Claude's reviewer agent on a PR with local worktree checkout and iterative review support
- **When to use**: Running a structured code review on a PR; supports re-reviews that track resolved vs open findings. Use --self for self-review before PR creation. For multiple PRs, dispatch separate Agent subagents — do not background with &
- **Usage**: `claude-review <pr_url_or_number>  |  claude-review --no-post <pr_url_or_number>  |  claude-review --self [<pr_url_or_number>]`

### claude-rules
Manages local Claude Code rule additions not tracked in the workbench
- **When to use**: Adding machine-specific or project-specific Claude instructions
- **Usage**: `claude-rules add <domain> "rule"  |  claude-rules list  |  claude-rules status`

### pr
Unified PR lifecycle CLI — CI failures, code review, and review comments
- **When to use**: Managing PR lifecycle: checking CI status, running reviews, addressing comments, or viewing unified PR status
- **Usage**: `pr status  |  pr ci [--fix]  |  pr review [--self] [--fix] [--post] [--repair] [--summary]  |  pr comments [--triage] [--fix] [--resolve]  |  pr fix  |  pr rebase [--fix] [--push] [--abort]  |  pr gc`
- **dream-scan** — Scan session transcripts and memory state for dream consolidation
- **dream-verify** — Verify dream memory file integrity across all projects
- **promote-scan** — Scan memories and workbench artifacts for promotion evaluation
- **retro-scan** — Scan PR review comments and cross-reference against coding rules
- **otto-log** — Query trail files across otto-workbench AI scripts — structured audit trail of actions, decisions, and errors

## AI Tooling
- **rtk** — CLI proxy that compresses command output to reduce LLM token consumption — auto-active via PreToolUse hook

## Serena Scripts

### serena-mcp
Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence
- **When to use**: Enabling Serena LSP-based code intelligence in a specific project
- **Usage**: `serena-mcp init  |  serena-mcp status`

## Workbench Dev Scripts
- **validate-registries** — Validates all tool registry YAML files for schema correctness and cross-file consistency
- **validate-components** — Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency
- **validate-migrations** — Validates migration file naming, function naming, and shebang conventions
- **validate-errexit** — Validates bash scripts for dangerous && patterns that silently exit under set -e
- **validate-skills** — Validates SKILL.md frontmatter conventions — required fields, name/directory consistency, lifecycle field pairing
- **validate-cli-flags** — Validates CLI flag conventions — no --repo alias, --pr/--branch mutual exclusivity
- **generate-tool-context** — Generates ai/guidelines/rules/tools.generated.md from the domain registries

## Dev Tools

### linear
Linear CLI (schpet/linear-cli) — manage Linear issues from the terminal
- **When to use**: Listing, creating, or starting Linear issues; git-aware issue tracking
- **Usage**: `linear issue list  |  linear issue view <ID> --json  |  linear issue create --team <KEY>  |  linear issue relation add <ID> <type> <relatedID>`
- **mas** — Mac App Store CLI — search, install, and update App Store apps from the terminal
