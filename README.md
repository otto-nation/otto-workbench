# Workbench

Personal development environment — shell config, utilities, and AI-powered git automation.

## Quick Start

```bash
git clone https://github.com/otto-nation/otto-workbench ~/workbench
cd ~/workbench
./install.sh
exec zsh
```

The installer symlinks scripts, zsh configs, and the global Taskfile, optionally installs Homebrew packages, and prompts to configure AI tools.

## What's Included

### Scripts (`bin/`)

| Script | Description |
|--------|-------------|
| `aliases` | Display all configured aliases and functions |
| `cleanup-testcontainers` | Clean up Docker testcontainers |
| `get-secret` | Interactive AWS Secrets Manager retrieval |
| `mem-analyze` | System memory analysis report |

Add a description to any new script by making line 2 a comment starting with a capital letter.

### ZSH Configuration

`zsh/.zshrc` is copied to `~/.zshrc` on first install. It sets up oh-my-zsh, lazy-loading for pyenv/nvm/SDKMAN, arch-aware Homebrew prefix, and modular config loading from `~/.config/zsh/config.d/`.

**Secrets and machine-specific config** go in `~/.env.local` — sourced automatically, never committed:

```bash
export JIRA_API_TOKEN=your-token
export CONTEXT7_API_KEY=ctx7sk-your-key
```

### Git Configuration

Useful aliases and settings in `git/.gitconfig`.

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

Prompts which tools to configure (Claude Code, Kiro), then runs each step with individual confirmation. Safe to re-run.

### What gets installed

**Claude Code:**
- `~/.claude/settings.json` — permissions and deny rules (merged, not overwritten)
- `~/.claude/CLAUDE.md` — coding guidelines
- `~/.claude/skills/` — skill definitions
- `~/.claude/agents/ci-cd` — commit and PR agent
- MCP servers: Serena, Sequential Thinking, Context7

**Kiro:**
- `~/.kiro/steering/` — coding guidelines
- `~/.kiro/agents/default.json` and `ci-cd.json` — agent configs with Serena, Sequential Thinking, and Context7

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
