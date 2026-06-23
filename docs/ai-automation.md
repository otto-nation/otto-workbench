# AI Automation

Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.

## Setup

First-time setup:

```bash
ai/setup.sh
```

Prompts for confirmation at each step. Safe to re-run. This installs Claude Code configuration, rules, skills, and agents.

After setup, configure the AI tool for global task automation:

```bash
task --global ai:setup
```

This creates `~/.config/task/taskfile.env` with:
- `AI_COMMAND` — which AI tool to use (e.g., `claude -p --agent ci-cd`)
- `GH_TOKEN` — GitHub PAT for PR automation (fine-grained, scoped to specific repos)
- `ANTHROPIC_API_KEY` — optional, for isolating automation API usage

## What Gets Installed

<!-- AI-INSTALLS-START -->
**Claude Code:**
- `~/.claude/settings.json` — permissions and deny rules (merged, not overwritten)
- `~/.claude/CLAUDE.md` — coding guidelines
- `~/.claude/rules/` — language and tool-specific rules (symlinked)

**Skills:** analyze-project, anatomy, ci-failures, context, dream, machine, pr-comments, pr-rebase, promote, retro, self-review-fix — see [Skill Reference](#skill-reference) for invocation, output, and lifecycle details.

**Agents:**

| Agent | Description |
|-------|-------------|
| changelog | Generate categorized release notes and changelogs from git history. Used by task automation. |
| ci-cd | Generate commit messages and pull request descriptions from git context. Used by task automation. |
| debugger | Systematic code-level bug diagnosis. Read-only — traces through source code to find root causes. Never modifies anything. |
| explain | Fast text-in/text-out explainer. Answers questions from provided input without exploring files or suggesting edits. |
| incident | Structured production incident investigation. Read-only triage — gathers symptoms, checks recent changes, forms ranked hypotheses. Never modifies anything. |
| migrate | Analyze codebases for migration tasks and produce phased upgrade plans. Read-only — plans changes but does not apply them. |
| reviewer | Structured code review for PRs and diffs. Read-only — produces categorized findings (must-fix, should-fix, nit). Never modifies anything. |
<!-- AI-INSTALLS-END -->

<!-- SKILL-REFERENCE-START -->
## Skill Reference

### `/analyze-project`

Analyze a project's codebase and populate scaffolded .claude/CLAUDE.md and .claude/rules/ files with project-specific conventions. TRIGGER when: user runs otto-workbench ai init, re-scaffolds with --force, or has empty .claude/CLAUDE.md or .claude/rules/ sections.

```
/analyze-project
```

**Output:** `.claude/CLAUDE.md, .claude/rules/`
**Trigger:** Run after otto-workbench ai init scaffolds a project, after --force re-scaffolds, or when .claude/CLAUDE.md or .claude/rules/ files have empty sections.

### `/anatomy`

Generate or refresh a project file index (.claude/anatomy.md) with per-file descriptions and token estimates. Helps Claude decide what to read before exploring. TRIGGER when: user wants an overview of codebase structure, before exploring an unfamiliar project, or after significant file changes. SKIP: user asks about a specific known file — read it directly.

```
/anatomy
```

**Output:** `.claude/anatomy.md`
**Auto-trigger:** on HEAD change (via Stop hook)
**Trigger:** Run to refresh the project file index before exploring an unfamiliar codebase, or after significant file changes.
**Skip:** Do not use when the user asks about a specific file they already know — just read it directly.

### `/ci-failures [<pr_number_or_run_id_or_branch>]`

Diagnose and fix GitHub Actions CI failures with run-aware progression tracking: fetch, classify, diagnose, fix, push, and monitor across workflow runs. TRIGGER when: user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red. SKIP: reviewing code (use code-review or pr review instead); addressing PR review comments (use pr-comments instead).

```
/ci-failures [<pr_number_or_run_id_or_branch>]
```
**Trigger:** Use when user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red.
**Skip:** Do not use for code review (use code-review or pr review instead); do not use for addressing PR review comments (use pr-comments instead).

### `/context`

On-demand context.md refresh. Reads recent sessions and memory to identify architectural facts that are missing or stale, then proposes specific additions to .claude/context.md. TRIGGER when: user discovers wrong-software assumptions, adds a new service or role, or context.md is stale (last-reviewed >14 days). SKIP: memory consolidation (use dream); machine-level facts (use machine).

```
/context
```

**Output:** `.claude/context.md`
**Trigger:** Run after discovering wrong-software assumptions, adding a new service or role to a project, when context.md last-reviewed date is more than 14 days old, or after discovering container tool constraints.
**Skip:** Do not use for memory consolidation (use dream instead) or machine-level facts (use machine instead).

### `/dream`

Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. TRIGGER when: user asks to consolidate memory, clean up notes, or after sessions with corrections and decisions. SKIP: project architecture facts (use context); machine profile updates (use machine).

```
/dream
```

**Output:** `memory/ topic files`
**Auto-trigger:** 24h (via Stop hook)
**Trigger:** Run to consolidate scattered memory notes, after multiple sessions with corrections or decisions, or when MEMORY.md is cluttered. Auto-triggers every 24h.
**Skip:** Do not use for project architecture facts (use context instead) or machine profile updates (use machine instead).

### `/machine`

Refresh the machine profile (~/.claude/machine/machine.md) — hardware, OS, runtimes, Docker, Git identity, and project registry. TRIGGER when: user upgrades tools, installs new runtimes, or machine.md is stale (>7 days). SKIP: project-specific context (use context); memory consolidation (use dream).

```
/machine
```

**Output:** `~/.claude/machine/machine.md`
**Auto-trigger:** 24h (via Stop hook)
**Trigger:** Run after upgrading runtimes, installing new tools, or when machine.md last-updated is more than 7 days old. Auto-triggers every 24h.
**Skip:** Do not use for project-specific context (use context instead) or memory consolidation (use dream instead).

### `/pr-comments [<pr_number_or_branch>]`

Analyze and address PR review comments with lifecycle tracking: fetch, classify, verify, fix, reply, and resolve across multi-round review cycles. TRIGGER when: user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments. SKIP: initial code review requests (use code-review or pr review instead); self-review before PR creation (use self-review-fix instead).

```
/pr-comments [<pr_number_or_branch>]
```
**Trigger:** Use when user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments.
**Skip:** Do not use for initial code review requests (use code-review or pr review instead); do not use for self-review before PR creation (use self-review-fix instead).

### `/pr-rebase [--fix]`

AI-assisted rebase onto origin/main with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead).

```
/pr-rebase [--fix]
```
**Trigger:** Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase.
**Skip:** Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording (use task commit:reword instead).

### `/promote`

Reviews accumulated Claude Code memories for promotion into durable workbench artifacts — lint rules, scripts, coding rules, hooks. Prioritizes mechanical enforcement over prose. TRIGGER when: user wants to review memories for promotion, or after dream has consolidated corrections. SKIP: direct rule/script edits — just edit them; memory consolidation (use dream).

```
/promote
```

**Output:** `ai/memory/PROMOTE.md`
**Auto-trigger:** 7 days (via Stop hook)
**Trigger:** Run to evaluate accumulated memories for promotion into workbench artifacts, or after dream has consolidated several sessions of corrections and decisions. Auto-triggers every 7 days.
**Skip:** Do not use when the user wants to directly edit a rule or script — just edit it. Do not use for memory consolidation (use dream instead).

### `/retro`

Analyze PR review comments to identify gaps in coding rules. Fetches comments from all registered repos, classifies them against existing rules, and proposes specific rule additions or refinements. TRIGGER when: user wants to analyze review patterns for rule gaps, after a batch of PR reviews. SKIP: addressing comments on a specific PR (use pr-comments); memory consolidation (use dream).

```
/retro
```

**Output:** `ai/memory/RETRO.md`
**Auto-trigger:** 72h (via Stop hook)
**Trigger:** Run to analyze recent PR review comments for coding rule gaps, after a round of PR reviews has been completed, or when rule coverage feels incomplete. Auto-triggers every 72h.
**Skip:** Do not use when the user wants to address comments on a specific PR (use pr-comments instead). Do not use for memory consolidation (use dream instead).

### `/self-review-fix [branch_name]`

Run self-review and auto-fix findings. Wraps pr review --self --fix. Can also fix from an existing review without re-running. TRIGGER when: user asks to self-review a branch, run pre-merge review, or auto-fix findings before PR creation. SKIP: reviewing someone else's PR (use code-review or review); addressing existing PR review comments (use pr-comments).

```
/self-review-fix [branch_name]
```
**Trigger:** Use when the user asks to self-review a branch, run a pre-merge review, or auto-fix review findings before creating a PR.
**Skip:** Do not use for reviewing someone else's PR (use code-review or review instead). Do not use for addressing existing PR review comments (use pr-comments instead).
<!-- SKILL-REFERENCE-END -->

<!-- LIFECYCLE-START -->
## Session Lifecycle

Skills with a cadence (shown in the table above) auto-trigger via Stop hooks in `settings.json`:

1. **Session exit** — Stop hooks run `should-<skill>.sh` cooldown checks. If due, creates `~/.claude/.<skill>-pending`
2. **Next session start** — Claude reads CLAUDE.md, sees the pending flag, runs the skill as a background subagent, then deletes the flag
3. **Skill completion** — completion script records a timestamp so the cooldown resets

Additionally, `wt-cleanup --quiet` runs on every session exit to remove stale git worktrees.

### Manual triggers

All lifecycle skills can be run on demand: `/dream`, `/promote`, `/retro`, `/anatomy`, `/machine`.
<!-- LIFECYCLE-END -->

## Task Automation

Use `--global` to run tasks from `~/.config/task/` rather than a local project Taskfile.

<!-- TASKS-BLOCK-START -->
```bash
task --global ai:setup             # Setup AI configuration
task --global commit               # Generate AI-powered commit message based on staged changes
task --global commit:reword        # Reword a commit message with AI (default: HEAD; or: task reword -- SHA)
task --global pr:content           # Preview AI-generated PR title and description (-- --no-issue to skip issue prompts, -- --base <branch> to target a non-default base)
task --global pr:create            # Create AI-powered pull request (-- --no-issue, --draft, --base <branch>, --title <title>, --body <body>, --body-file <path>)
task --global pr:update            # Update current PR description (-- --no-issue, --base <branch>, --title <title>, --body <body>)
task --global review               # AI review of staged, unstaged, and committed branch changes
task --global pr:review            # AI review of the current PR
```
<!-- TASKS-BLOCK-END -->

## Configuration

Override which AI tool the global Taskfile uses:

```bash
# ~/.config/task/taskfile.env
AI_COMMAND=claude -p --agent ci-cd
```

Override per-project with `.taskfile/taskfile.env` in a project root.

### Running from a different directory

All global tasks default to running in the current working directory. When your CWD is not the target repo (e.g., running from a Claude Code session rooted in a different project), pass `REPO_DIR`:

```bash
task --global REPO_DIR=/path/to/worktree pr:create -- --no-issue
task --global REPO_DIR=/path/to/worktree commit
```

## Guidelines & Rules

The workbench installs a layered rule system into Claude Code:

- **Global guidelines** ([`ai/guidelines/`](../ai/guidelines/)) — universal coding principles, language-specific rules
- **Tool rules** ([`ai/guidelines/rules/`](../ai/guidelines/rules/)) — path-scoped rules that auto-load based on file type
- **Generated rules** — [`tools.generated.md`](../ai/guidelines/rules/tools.generated.md) and [`git.generated.md`](../ai/guidelines/rules/git.generated.md) are derived from registries and conventions

Rules are symlinked to `~/.claude/rules/` during sync. Add machine-specific rules with:

```bash
claude-rules add <domain> "rule text"    # add a local rule
claude-rules list                        # show all rules
claude-rules status                      # check sync status
```

## Scaffolding a New Project

After cloning a repo, scaffold Claude Code configuration for it:

```bash
otto-workbench ai init          # scaffold .claude/ in the current repo
otto-workbench ai init --force  # re-scaffold an existing project
```

This creates a `.claude/` directory with stack-detected rules and a project anatomy file (file index with token estimates).
