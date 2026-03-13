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

Secrets and machine-specific env vars go in `~/.env.local` — sourced automatically, never committed.

## Keeping in Sync

After pulling updates or when config drifts:

```bash
otto-workbench sync
```

Re-applies all symlinks, regenerates tool context, and syncs Claude settings and rules. Safe to run at any time.

Re-run a single component independently: `bash <component>/setup.sh` (e.g. `bash ai/setup.sh`)

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
| `generate-git-rules` | Regenerates git.generated.md from lib/ai-commit.sh constants |
| `validate-registries` | Validates all tool registry YAML files for schema correctness and cross-file consistency |
<!-- SCRIPTS-TABLE-END -->

### ZSH Configuration

`zsh/.zshrc` is copied to `~/.zshrc` on first install. It sets up oh-my-zsh, lazy-loading for pyenv/nvm/SDKMAN, arch-aware Homebrew prefix, and modular config loading from `~/.config/zsh/config.d/`.

`zsh/starship.toml` is symlinked to `~/.config/starship.toml`.

**Secrets and machine-specific config** go in `~/.env.local` — sourced automatically, never committed:

```bash
export JIRA_API_TOKEN=your-token
export CONTEXT7_API_KEY=ctx7sk-your-key
```

### Git Configuration

Useful aliases and settings in `git/.gitconfig`. `core.fsmonitor` and `core.untrackedCache` are enabled globally for fast `git status`.

### Tool Registry

Each tooling directory owns a `registry.yml` describing the tools it provides — name, description, when to use, usage, and docs URL. These are combined into `ai/guidelines/rules/tools.generated.md` and auto-loaded into every Claude and Kiro session.

`ai/guidelines/rules/git.generated.md` is generated from the commit/PR constants in `lib/ai-commit.sh` — commit types, header length limit, PR template, and branch naming conventions. Never edit generated files directly.

Update the registry after adding or removing a tool, then regenerate:

```bash
generate-tool-context   # regenerates tools.generated.md
generate-git-rules      # regenerates git.generated.md (or: otto-workbench sync)
```

### Homebrew Packages

- `brew/Brewfile` — core packages for any personal dev machine
- `brew/work/` — opt-in per stack: `aws`, `java`, `terraform`, `kubernetes`, `jira`

```bash
brew bundle --file=brew/Brewfile
brew bundle --file=brew/work/aws.Brewfile  # add work stacks as needed
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
- Skills: go
- Agents: ci-cd
- MCPs: Context7, Sequential Thinking, Serena

**Kiro:**
- `~/.kiro/steering/` — language, tool, and git rules (symlinked from `ai/guidelines/rules/`)
- Agents: ci-cd, default
<!-- AI-INSTALLS-END -->

**Context7** reads `CONTEXT7_API_KEY` from the environment at runtime — add it to `~/.env.local`.

### AI command configuration

After setup, configure which AI tool the global Taskfile uses:

```bash
# ~/.config/task/taskfile.env
AI_COMMAND=claude -p --agent ci-cd --strict-mcp-config
AI_COMMAND=kiro-cli chat --no-interactive --agent ci-cd
```

Override per-project with `.taskfile/taskfile.env` in a project root.

## Requirements

- macOS, ZSH
- [Task](https://taskfile.dev) — auto-installed by `install.sh`
- [gh](https://cli.github.com) — for `pr:create` and `pr:update`
- Docker, AWS CLI — optional, for container and AWS utilities

**Manual installs** (run once on a new machine):

```bash
# oh-my-zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# SDKMAN (Java version manager)
curl -s "https://get.sdkman.io" | bash
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, testing, and code conventions.
