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

**Skills:**

| Skill | Description | Cadence | Scope |
|-------|-------------|---------|-------|
| analyze-project | Analyze a project's codebase and populate scaffolded .claude/CLAUDE.md and .claude/rules/ files with project-specific conventions. Run after scaffolding a new project. | — | — |
| anatomy | Generate or refresh a project file index (.claude/anatomy.md) with per-file descriptions and token estimates. Helps Claude decide what to read before exploring. | on HEAD change | per-project |
| context | On-demand context.md refresh. Reads recent sessions and memory to identify architectural facts that are missing or stale, then proposes specific additions to .claude/context.md. | — | — |
| dream | Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. Inspired by how sleep consolidates human memory. | 24h | per-project |
| machine | Refresh the machine profile (~/.claude/machine/machine.md) — hardware, OS, runtimes, Docker, Git identity, and project registry. Run after upgrading tools or to force a refresh. | 24h | global |
| pr-comments | Analyze and address PR review comments with lifecycle tracking: fetch, classify, verify, fix, reply, and resolve across multi-round review cycles. | — | — |
| pr-review | Manage GitHub PR review lifecycle: analyze unanswered threads, update review files, and post replies. Initial posting is handled by the review-post script. | — | — |
| promote | Reviews accumulated Claude Code memories for promotion into durable workbench artifacts — lint rules, scripts, coding rules, hooks. Prioritizes mechanical enforcement over prose. | 7 days | per-project |
| retro | Analyze PR review comments to identify gaps in coding rules. Fetches comments from all registered repos, classifies them against existing rules, and proposes specific rule additions or refinements. | 72h | global |
| self-review-fix | Run self-review and auto-fix findings. Wraps claude-review --self --fix. Can also fix from an existing review without re-running. | — | — |

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

## Skill Reference

### `/analyze-project`

Analyze a project's codebase and populate scaffolded `.claude/CLAUDE.md` and `.claude/rules/` files with project-specific conventions.

```
/analyze-project
```

No arguments. Run after `otto-workbench ai init` scaffolds a new project. Three phases: DISCOVER (scan identity, paths, workflow, patterns) → PROPOSE (draft content with user confirmation) → APPLY (write to scaffolded files).

**Output:** `.claude/CLAUDE.md`, `.claude/rules/`

### `/anatomy`

Generate or refresh a project file index with per-file descriptions and token estimates.

```
/anatomy
```

No arguments. Scans `git ls-files`, extracts first meaningful comment (lines 1–15), estimates tokens as `lines × 4`, writes markdown table grouped by directory. Skips binary files, lock files, and generated code.

**Output:** `.claude/anatomy.md`
**Auto-trigger:** on git HEAD change (via Stop hook)

### `/context`

Refresh `.claude/context.md` with architectural facts from recent sessions and memory.

```
/context
```

No arguments. Lighter than `/dream` — focuses only on context.md, not memory consolidation. Identifies wrong-software discoveries, tool availability findings, architectural confirmations, and new services. Proposes additions with evidence before applying.

**Output:** `.claude/context.md`

### `/dream`

Memory consolidation — scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files.

```
/dream
```

No arguments. Five phases: ORIENT → GATHER SIGNAL → CONSOLIDATE → PRUNE & INDEX → CONTEXT UPDATE. Routes facts to the correct destination (`machine.md`, `context.md`, or `memory/`). Runs `dream-verify` for integrity checks.

**Output:** `memory/` topic files (preferences, decisions, corrections, patterns, review-feedback)
**Auto-trigger:** every 24h (via Stop hook)

### `/machine`

Refresh the machine profile with hardware, OS, runtimes, Docker, Git identity, and project registry.

```
/machine
```

No arguments. Regenerates unconditionally, bypassing the 24h staleness check. Run after upgrading tools or when `<!-- last-updated -->` is more than 7 days old.

**Output:** `~/.claude/machine/machine.md`
**Auto-trigger:** every 24h (via Stop hook)

### `/pr-comments`

Analyze and address PR review comments with lifecycle tracking.

```
/pr-comments [<pr_number>]
```

| Argument / Flag | Description |
|-----------------|-------------|
| `<pr_number>` | PR number (optional — auto-detects from current branch) |
| `--repo <OWNER/REPO>` | Repository |
| `--repo-dir <PATH>` | Git worktree directory (aliases: `--worktree`) |
| `--resolve-verified` | Resolve all verified threads on GitHub |

Eight steps: Fetch → Classify (actionable/question/approval/conflicting) → Verify suggestions against codebase → Apply fixes → Reply inline → Resolve verified threads → Emit feedback signals → Report. Tracks thread lifecycle: new → addressed → verified → resolved (or contested → re-addressed).

### `/pr-review`

Manage GitHub PR review lifecycle — analyze unanswered threads, update review files, and post replies.

```
/pr-review <pr_number>
```

`<pr_number>` is required. Works with existing PENDING reviews or submitted reviews with unanswered threads. Updates review file at `~/.config/workbench/reviews/<repo>-<pr_number>.md`.

### `/promote`

Review accumulated memories for promotion into durable workbench artifacts.

```
/promote
```

No arguments. Four phases: ORIENT → EVALUATE → PROPOSE → RECORD. Uses priority ladder: Lint rules → Scripts/Automation → Coding rules → Agent/Skill updates → Keep as memory → Delete.

**Output:** `ai/memory/PROMOTE.md` (read-only report — user reviews and applies manually)
**Auto-trigger:** every 7 days (via Stop hook)

### `/retro`

Analyze PR review comments to identify gaps in coding rules.

```
/retro
```

No arguments. Four phases: ORIENT → CLASSIFY (rule-gap, rule-refinement, false-negative, one-off, noise) → PROPOSE → REPORT. Elevates patterns appearing 2+ times.

**Output:** `ai/memory/RETRO.md` (read-only report — user reviews and applies manually)
**Auto-trigger:** every 72h (via Stop hook)

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
