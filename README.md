# otto-workbench

Personal development environment — shell config, utilities, and AI-powered git automation.

## Quick Start

```bash
git clone https://github.com/otto-nation/otto-workbench ~/otto-workbench
cd ~/otto-workbench
./install.sh
exec zsh
```

The installer symlinks scripts, zsh configs, git config, and the global Taskfile, then presents an optional component menu (<!-- COMPONENT-MENU-START -->Homebrew packages, Docker, Terminals, Editors, AI tools<!-- COMPONENT-MENU-END -->).

## After Install

<!-- AFTER-INSTALL-START -->
1. **Reload your shell**: `exec zsh`
2. **Docker** (if installed): start your runtime — `colima start` or launch OrbStack
3. **AI tools** (if installed): run `task --global ai:setup` to configure your `AI_COMMAND` and tokens
<!-- AFTER-INSTALL-END -->

Secrets and machine-specific env vars go in `~/.env.local` — sourced first by the shell loader, never committed. See `zsh/.env.local.template`.

## Keeping in Sync

<!-- WORKBENCH-COMMANDS-START -->
| Command | Scope | Description |
|---------|-------|-------------|
| `otto-workbench sync` | All components | Re-applies all workbench config — runs pending migrations, re-symlinks scripts and configs, regenerates tool context, and syncs AI settings. Safe to re-run at any time. |
| `otto-workbench claude` | Claude only | Syncs machine-level Claude config, then scaffolds a `.claude/` directory in the current git repo (if one doesn't exist) with stack-detected rules and a project anatomy file. |
| `otto-workbench claude --force` | Claude only | Re-scaffolding an existing project's `.claude/` |
| `otto-workbench changelog` | Git history | Reviewing recent changes from conventional commits |
<!-- WORKBENCH-COMMANDS-END -->

## File Layout

Both `install.sh` and `otto-workbench sync` print a summary of everything below.

### Managed files (updated by `otto-workbench sync`)

These are owned by the workbench and updated every time you sync. Do not edit directly.

| Target | Source | Method |
|--------|--------|--------|
| `~/.local/bin/*` | `bin/` | symlinked |
| `~/.config/zsh/config.d/*/` | `zsh/config.d/*/` | copied |
| `~/.config/zsh/config.d/loader.zsh` | `zsh/config.d/loader.zsh` | copied |
| `~/.config/starship.toml` | `zsh/starship.toml` | copied |
| `~/.gitconfig` | includes `git/gitconfig.shared` | include stanza |
| `~/.git-hooks/*` | `git/hooks/` | symlinked |
| `~/.config/task/{Taskfile.yml,lib/}` | `Taskfile.global.yml`, `lib/` | symlinked |
| `~/.claude/*` | `ai/claude/` | mixed (merge/copy/symlink) |

### Editable configs (yours — never overwritten)

These are created once (from templates or by first-time setup) and never modified by sync.

| File | Purpose | Bootstrap |
|------|---------|-----------|
| `~/.gitconfig` | Git identity, GPG, credentials | `git/gitconfig.template` |
| `~/.env.local` | Shell secrets, API keys, env overrides | `zsh/.env.local.template` |
| `~/.config/task/taskfile.env` | AI automation tokens (`GH_TOKEN`, `AI_COMMAND`) | `task --global ai:setup` |
| `~/.zshrc` | Shell rc file | `zsh/.zshrc` |
| `~/.config/ghostty/config` | Terminal config | `terminals/ghostty/config.template` |

## What's Included

### Scripts (`bin/`)

<!-- SCRIPTS-TABLE-START -->
| Script | Description |
|--------|-------------|
| `task` | AI-powered Git automation runner; wraps go-task with global/local Taskfile routing |
| `otto-workbench` | Manage your workbench developer environment |
| `mem-analyze` | macOS memory analysis report — pressure, swap usage, top processes, per-user totals |
| `get-secret` | Interactively retrieves a secret from AWS Secrets Manager by listing and selecting |
| `validate-registries` | Validates all tool registry YAML files for schema correctness and cross-file consistency |
| `validate-components` | Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency |
| `validate-migrations` | Validates migration file naming, function naming, and shebang conventions |
| `claude-rules` | Manages local Claude Code rule additions not tracked in the workbench |
| `generate-tool-context` | Generates ai/guidelines/rules/tools.generated.md from the domain registries |
| `serena-mcp` | Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence |
| `cleanup-testcontainers` | Stops and removes stale Testcontainers Docker resources left by test runs |
| `generate-changelog` | Generates a changelog from conventional commits grouped by type |
| `generate-git-rules` | Regenerates git.generated.md from lib/conventions.sh constants |
| `ghostty-terminfo-push` | Installs Ghostty's xterm-ghostty terminfo on a remote host — fixes 'Error opening terminal' over SSH |
| `aliases` | Lists all custom shell aliases and functions with optional keyword filtering |
<!-- SCRIPTS-TABLE-END -->

### ZSH Configuration

`zsh/.zshrc` is copied to `~/.zshrc` on first install. It sets up oh-my-zsh, lazy-loading for pyenv/nvm/SDKMAN, arch-aware Homebrew prefix, and modular config loading from `~/.config/zsh/config.d/`.

oh-my-zsh and SDKMAN are loaded lazily and skipped gracefully if absent — install them separately if you want them:

```bash
# oh-my-zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# SDKMAN (Java version manager)
curl -s "https://get.sdkman.io" | bash
```

`zsh/starship.toml` is symlinked to `~/.config/starship.toml`.

**Secrets and machine-specific config** go in `~/.env.local` — sourced first by the shell loader, never committed. Created automatically on first install; the auto-generated ENV section is updated on every sync.

Examples:
```bash
export JIRA_API_TOKEN=your-token
export COLIMA_CPU=6
```

> **Note:** AI automation tokens (`GH_TOKEN`, `ANTHROPIC_API_KEY`) belong in `~/.config/task/taskfile.env`, not here — they must stay isolated from your interactive shell. Run `task --global ai:setup` to configure them.

### Git Configuration

Two-layer architecture: `~/.gitconfig` is your machine-specific file (identity, GPG, credentials) and includes `git/gitconfig.shared` for shared aliases, colors, and behavior. `git config --global` writes to `~/.gitconfig` as expected — no drift. `core.fsmonitor` and `core.untrackedCache` are enabled globally for fast `git status`.

### Tool Registry

Each tooling directory owns a `registry.yml` describing the tools it provides — name, description, when to use, usage, and docs URL. These are combined into `ai/guidelines/rules/tools.generated.md` and auto-loaded into every Claude session.

`ai/guidelines/rules/git.generated.md` is generated from the commit/PR constants in `lib/ai/core.sh` — commit types, header length limit, PR template, and branch naming conventions. Never edit generated files directly.

Update the registry after adding or removing a tool, then regenerate:

```bash
generate-tool-context   # regenerates tools.generated.md
generate-git-rules      # regenerates git.generated.md (or: otto-workbench sync)
```

### Tools

<!-- TOOLS-TABLE-START -->

**Brew Tools**

| Tool | Description |
|------|-------------|
| [docker](https://docs.docker.com/engine/reference/commandline/cli/) | Docker CLI — build, run, and manage containers against any backend runtime |
| [jq](https://jqlang.github.io/jq/manual/) | JSON processor for querying, filtering, and transforming JSON data |
| [yq](https://mikefarah.gitbook.io/yq/) | YAML/JSON/TOML processor — like jq but for YAML |
| [gh](https://cli.github.com/manual/) | GitHub CLI — manage PRs, issues, repos, checks, and releases from the terminal |
| [go-task](https://taskfile.dev) | Task runner with YAML-defined tasks (used via the 'task' wrapper script) |
| [shellcheck](https://www.shellcheck.net/) | Static analysis tool for shell scripts — catches bugs and style issues |
| [bats-core](https://bats-core.readthedocs.io/) | Bash Automated Testing System — unit testing framework for shell scripts |
| [parallel](https://www.gnu.org/software/parallel/) | GNU parallel — run shell commands in parallel across multiple cores |
| [tree](https://oldmanprogrammer.net/source.php?dir=projects/tree) | Recursive directory listing tool — visualizes folder structure as a tree |
| [delta](https://dandavison.github.io/delta/) | Syntax-highlighting pager for git diffs — word-level diff highlighting with line numbers |
| [pipx](https://pipx.pypa.io/) | Install and run Python CLI tools in isolated environments |
| [uv](https://docs.astral.sh/uv/) | Fast Python package and project manager (Rust-based pip/venv replacement) |
| [gitleaks](https://github.com/gitleaks/gitleaks) | Secret scanner — detects committed credentials, tokens, and keys |

**Version Management**

| Tool | Description |
|------|-------------|
| [mise](https://mise.jdx.dev) | Polyglot dev tool version manager — replaces nvm, jenv, pyenv, asdf with one tool |

**Mac Apps**

| Tool | Description |
|------|-------------|
| [1password-cli](https://developer.1password.com/docs/cli/) | 1Password CLI (op) — access secrets, SSH keys, and vaults from the terminal |
| [1password](https://1password.com/) | 1Password — password manager and secure vault for credentials, keys, and secrets |
| [bruno](https://www.usebruno.com/) | Open-source API client — test and document REST, GraphQL, and gRPC APIs |
| [ghostty](https://ghostty.org/) | Ghostty — fast, native terminal emulator with GPU rendering |
| [gitkraken](https://www.gitkraken.com/) | GitKraken — visual Git client for branch management, history, and merge conflict resolution |
| [readdle-spark](https://sparkmailapp.com/) | Spark — email client by Readdle with smart inbox, snooze, and team collaboration |
| [spotify](https://www.spotify.com/) | Spotify — music and podcast streaming client |
| [tailscale](https://tailscale.com/kb/) | Tailscale — zero-config mesh VPN built on WireGuard for secure private networking |
| [zed](https://zed.dev/) | Zed — high-performance, multiplayer code editor built in Rust |

**AWS Tools**

| Tool | Description |
|------|-------------|
| [aws](https://docs.aws.amazon.com/cli/) | AWS CLI — manage AWS resources, services, and credentials from the terminal |
| [aws-sso-util](https://github.com/benkehoe/aws-sso-util) | Utilities for AWS SSO — simplifies login and credential management for SSO-based AWS accounts |

**Kubernetes Tools**

| Tool | Description |
|------|-------------|
| [k9s](https://k9scli.io/) | Terminal UI for Kubernetes — real-time cluster monitoring and management |
| [kubectx](https://github.com/ahmetb/kubectx) | Fast Kubernetes context and namespace switcher |
| [kubectl](https://kubernetes.io/docs/reference/kubectl/) | Kubernetes CLI — manage clusters, deployments, pods, and services |

**Terraform Tools**

| Tool | Description |
|------|-------------|
| [tfenv](https://github.com/tfutils/tfenv) | Terraform version manager — install and switch between Terraform versions |
| [terraform-docs](https://terraform-docs.io/) | Generate documentation from Terraform module inputs and outputs |

**Go Tools**

| Tool | Description |
|------|-------------|
| [go](https://go.dev/doc/) | Go programming language toolchain — compiler, formatter, and standard tooling |
| [golangci-lint](https://golangci-lint.run/) | Fast Go linter runner — aggregates and runs many linters in one pass |
| [goreleaser](https://goreleaser.com/) | Go release automation — builds cross-platform binaries and publishes GitHub releases |

**Java Tools**

| Tool | Description |
|------|-------------|
| [gradle](https://docs.gradle.org/) | Gradle build tool — build, test, and publish JVM projects |
| [mvn](https://maven.apache.org/guides/) | Apache Maven — build and dependency management for Java projects |

**Signing Tools**

| Tool | Description |
|------|-------------|
| [gnupg](https://gnupg.org/documentation/) | GNU Privacy Guard — GPG encryption and signing |

**Shell Tools**

| Tool | Description |
|------|-------------|
| [starship](https://starship.rs) | Fast, cross-shell prompt — shows git status, language versions, and context at a glance |
| [fzf](https://github.com/junegunn/fzf) | Fuzzy finder — interactive search for files, history, and command output |
| [zoxide](https://github.com/ajeetdsouza/zoxide) | Smarter cd — learns frequently-visited directories and jumps to them by partial name |
| [zsh-history-substring-search](https://github.com/zsh-users/zsh-history-substring-search) | History search filtered by what you've typed — press up/down to cycle through matches |
| [zsh-completions](https://github.com/zsh-users/zsh-completions) | Additional completion definitions for zsh — extends tab-completion for many tools |
| [zsh-syntax-highlighting](https://github.com/zsh-users/zsh-syntax-highlighting) | Fish-style syntax highlighting for zsh — highlights valid commands green, errors red |

**Dev Tools**

| Tool | Description |
|------|-------------|
| [jira](https://github.com/ankitpokhrel/jira-cli) | Jira CLI (ankitpokhrel/jira-cli) — manage Jira tickets from the terminal |
| [linear](https://github.com/schpet/linear-cli) | Linear CLI (schpet/linear-cli) — manage Linear issues from the terminal |
| [mas](https://github.com/mas-cli/mas) | Mac App Store CLI — search, install, and update App Store apps from the terminal |
<!-- TOOLS-TABLE-END -->

## AI

First-time setup:

```bash
bash ai/setup.sh
```

Prompts for confirmation at each step. Safe to re-run.

### What gets installed

<!-- AI-INSTALLS-START -->
**Claude Code:**
- `~/.claude/settings.json` — permissions and deny rules (merged, not overwritten)
- `~/.claude/CLAUDE.md` — coding guidelines
- `~/.claude/rules/` — language and tool-specific rules (symlinked)

**Skills:**

| Skill | Description |
|-------|-------------|
| anatomy | Generate or refresh a project file index (.claude/anatomy.md) with per-file descriptions and token estimates. Helps Claude decide what to read before exploring. |
| dream | Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. Inspired by how sleep consolidates human memory. |

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

### Task automation

<!-- TASKS-BLOCK-START -->
```bash
task --global ai:setup             # Setup AI configuration
task --global commit               # Generate AI-powered commit message based on staged changes
task --global commit:reword        # Reword a commit message with AI (default: HEAD; or: task reword -- SHA)
task --global pr:content           # Preview AI-generated PR title and description (pass -- --no-issue to skip issue prompts)
task --global pr:create            # Create AI-powered pull request with smart title and description (pass -- --no-issue to skip issue prompts)
task --global pr:update            # Update current PR description with AI-generated content (pass -- --no-issue to skip issue prompts)
task --global review               # AI review of staged, unstaged, and committed branch changes
task --global pr:review            # AI review of the current PR
task --global wt:create            # Create a git worktree with auto-named branch (usage: task wt:create -- <name> [base-branch])
task --global wt:list              # List all worktrees in the current repo
task --global wt:status            # Show status of current or named worktree (usage: task wt:status [-- name])
task --global wt:open              # Print path to a worktree for cd (usage: cd $(task wt:open -- <name>))
task --global wt:remove            # Remove a worktree and optionally delete its branch (usage: task wt:remove -- <name> [--force])
```
<!-- TASKS-BLOCK-END -->

Use `--global` to run tasks from `~/.config/task/` rather than a local project Taskfile.

### Configuration

After setup, configure which AI tool the global Taskfile uses:

```bash
# ~/.config/task/taskfile.env
AI_COMMAND=claude -p --agent ci-cd
```

Override per-project with `.taskfile/taskfile.env` in a project root.

## Requirements

- macOS or Linux
- bash (to run `install.sh`)
- git (to clone the repo)

Everything else — Task, gh, Docker, and language tooling — is either auto-installed by `install.sh` or available through the optional component menu.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing, and code conventions.
