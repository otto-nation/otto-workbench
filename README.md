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
workbench sync
```

Re-applies all symlinks, regenerates tool context, and syncs Claude settings and rules. Safe to run at any time.

Re-run a single component independently: `bash <component>/setup.sh` (e.g. `bash ai/setup.sh`)

## What's Included

### Scripts (`bin/`)

| Script | Description |
|--------|-------------|
| `workbench` | Re-apply all workbench configuration to `~/` |
| `claude-rules` | Manage Claude Code rule additions |
| `aliases` | Display all configured aliases and functions |
| `get-secret` | Interactive AWS Secrets Manager retrieval |
| `cleanup-testcontainers` | Clean up Docker testcontainers |
| `mem-analyze` | System memory analysis report |
| `generate-tool-context` | Regenerate `tools.generated.md` from registries |
| `generate-git-rules` | Regenerate `git.generated.md` from `lib/ai-commit.sh` constants |
| `validate-registries` | Validate all tool registry YAML files |

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
generate-git-rules      # regenerates git.generated.md (or: workbench sync)
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

```bash
task --global commit           # AI-generated commit message
task --global commit:reword    # reword HEAD (or pass -- SHA)
task --global pr:content       # preview AI-generated PR title + body
task --global pr:create        # create PR (auto-pushes if needed)
task --global pr:update        # update existing PR description
task --global ai:setup         # configure AI command
```

Use `--global` to run tasks from `~/.config/task/` rather than a local project Taskfile.

## AI Tools Setup

```bash
bash ai/setup.sh
```

Prompts which tools to configure (Claude Code, Kiro), lists all steps upfront, then runs each with individual confirmation. Safe to re-run.

### What gets installed

**Claude Code:**
- `~/.claude/settings.json` — permissions and deny rules (merged, not overwritten)
- `~/.claude/CLAUDE.md` — coding guidelines
- `~/.claude/rules/` — language and tool-specific rules (symlinked)
- `~/.claude/skills/` — skill definitions
- `~/.claude/agents/` — agent configs
- MCP servers: Serena, Sequential Thinking, Context7, Datadog

**Kiro:**
- `~/.kiro/steering/` — language, tool, and git rules (symlinked from `ai/guidelines/rules/`)
- `~/.kiro/agents/` — agent configs with Serena, Sequential Thinking, and Context7

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
