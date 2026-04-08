# otto-workbench

Personal development environment — shell config, utilities, and AI-powered git automation.

## Quick Start

```bash
git clone https://github.com/otto-nation/otto-workbench ~/otto-workbench
cd ~/otto-workbench
./install.sh
exec zsh
```

The installer symlinks scripts, zsh configs, git config, and the global Taskfile, then presents an optional component menu (Homebrew, Docker, iTerm2, AI tools).

## After Install

1. **Reload your shell**: `exec zsh`
2. **iTerm2** (if installed): Settings → Profiles → Text → Font → `FiraCodeNFM-Reg`
3. **Docker** (if installed): start your runtime — `colima start` or launch OrbStack
4. **AI tools** (if installed): edit `~/.config/task/taskfile.env` to set your `AI_COMMAND`

Secrets and machine-specific env vars go in `~/.env.local` — sourced first by the shell loader, never committed. See `zsh/.env.local.template`.

## Keeping in Sync

After pulling updates or when config drifts:

```bash
otto-workbench sync
```

Runs pending migrations, re-applies all symlinks, regenerates tool context, and syncs Claude settings and rules. Prints a summary of all managed and editable files when complete. Safe to run at any time.

Re-run a single component independently:
- Optional components (interactive): `bash <component>/setup.sh` — e.g. `bash ai/setup.sh`
- Core components (idempotent): `bash <component>/steps.sh` — e.g. `bash git/steps.sh`

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
| `~/.kiro/*` | `ai/kiro/`, `ai/guidelines/` | mixed |

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
| `aliases` | Lists all custom shell aliases and functions with optional keyword filtering |
| `claude-rules` | Manages local Claude Code rule additions not tracked in the workbench |
| `otto-workbench` | Re-applies all workbench configuration to ~/ (symlinks, settings, rules) |
| `generate-tool-context` | Generates ai/guidelines/rules/tools.generated.md from the domain registries |
| `mem-analyze` | macOS memory analysis report — pressure, swap usage, top processes, per-user totals |
| `get-secret` | Interactively retrieves a secret from AWS Secrets Manager by listing and selecting |
| `cleanup-testcontainers` | Stops and removes stale Testcontainers Docker resources left by test runs |
| `generate-git-rules` | Regenerates git.generated.md from lib/ai/core.sh constants |
| `validate-registries` | Validates all tool registry YAML files for schema correctness and cross-file consistency |
| `validate-components` | Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency |
| `validate-migrations` | Validates migration file naming, function naming, and shebang conventions |
| `generate-changelog` | Generates a changelog from conventional commits grouped by type |
| `serena-mcp` | Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence |
| `ghostty-terminfo-push` | Installs Ghostty's xterm-ghostty terminfo on a remote host — fixes 'Error opening terminal' over SSH |
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

Each tooling directory owns a `registry.yml` describing the tools it provides — name, description, when to use, usage, and docs URL. These are combined into `ai/guidelines/rules/tools.generated.md` and auto-loaded into every Claude and Kiro session.

`ai/guidelines/rules/git.generated.md` is generated from the commit/PR constants in `lib/ai/core.sh` — commit types, header length limit, PR template, and branch naming conventions. Never edit generated files directly.

Update the registry after adding or removing a tool, then regenerate:

```bash
generate-tool-context   # regenerates tools.generated.md
generate-git-rules      # regenerates git.generated.md (or: otto-workbench sync)
```

### Homebrew Packages

<!-- BREW-STACKS-START -->
- `brew/Brewfile` — core: bash, jq, tree, yq, pipx, uv, docker, mise, bats-core, delta, gh, go-task, otto-stack, shellcheck, gitleaks, git-credential-manager
- `brew/apps/` — 1password, 1password-cli, bruno, ghostty, gitkraken, hiddenbar, maccy, readdle-spark, rectangle, spotify, sublime-text, zed
- `brew/infra/` — aws · kubernetes · terraform
- `brew/lang/` — go · java
- `brew/security/` — gnupg, pinentry-mac, gpg-suite
- `brew/shell/` — fzf, starship, font-fira-code-nerd-font, zoxide, zsh-history-substring-search, zsh-completions, zsh-syntax-highlighting
- `brew/tools/` — jira-cli, linear, mas
<!-- BREW-STACKS-END -->

```bash
brew bundle --file=brew/Brewfile
brew bundle --file=brew/infra/aws.Brewfile  # add optional stacks as needed
```

Keep the core Brewfile current after installing or removing packages:

```bash
task --global brew:dump
```

### Global Taskfile — AI Git Automation

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
```
<!-- TASKS-BLOCK-END -->

Use `--global` to run tasks from `~/.config/task/` rather than a local project Taskfile.

## AI Tools Setup

```bash
bash ai/setup.sh
```

Prompts which tools to configure (Claude Code, Kiro), lists all steps upfront, then runs each with individual confirmation. Safe to re-run.

### What gets installed

<!-- AI-INSTALLS-START -->
**Claude Code:**
- `~/.claude/settings.json` — permissions and deny rules (merged, not overwritten)
- `~/.claude/CLAUDE.md` — coding guidelines
- `~/.claude/rules/` — language and tool-specific rules (symlinked)
- Skills: dream
- Agents: changelog, ci-cd, explain, incident, migrate

**Kiro:**
- `~/.kiro/steering/` — language, tool, and git rules (symlinked from `ai/guidelines/rules/`)
- Agents: ci-cd, default
<!-- AI-INSTALLS-END -->

### AI command configuration

After setup, configure which AI tool the global Taskfile uses:

```bash
# ~/.config/task/taskfile.env
AI_COMMAND=claude -p --agent ci-cd
AI_COMMAND=kiro-cli chat --no-interactive --agent ci-cd
```

Override per-project with `.taskfile/taskfile.env` in a project root.

## Requirements

- macOS or Linux
- bash (to run `install.sh`)
- git (to clone the repo)

Everything else — Task, gh, Docker, and language tooling — is either auto-installed by `install.sh` or available through the optional component menu.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing, and code conventions.
