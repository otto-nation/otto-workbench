# Workbench

Personal development environment with shell configuration, utilities, and AI-powered git automation.

## Quick Start

```bash
git clone https://github.com/otto-nation/otto-workbench ~/workbench
cd ~/workbench
./install.sh
```

The installer will:
- Install [Task](https://taskfile.dev) if needed
- Symlink scripts to `~/.local/bin/`
- Symlink zsh configs to `~/.config/zsh/config.d/`
- Install global Taskfile for AI-powered git automation
- Prompt you to configure your AI command

## What's Included

### Custom Scripts
- `task` - Task runner (installed if not present)
- `cleanup-testcontainers` - Clean up Docker testcontainers
- `get-secret` - Interactive AWS Secrets Manager retrieval
- `mem-analyze` - System memory analysis report
- `show-aliases` - Display all configured aliases and functions

### ZSH Configuration
- Docker and container aliases (`d-*`)
- Kubernetes aliases (`k-*`)
- Git and development tools (`gw-*`, `gs`, `ga`, `gc`)
- AWS utilities
- System utilities and macOS helpers

### Git Configuration
- Useful git aliases and settings

### Global Taskfile (AI-Powered Git Automation)
- `task --global commit` - AI-generated commit messages
- `task --global create-pr` - AI-generated pull requests
- `task --global update-pr` - Update PR descriptions
- `task --global setup-ai` - Configure AI command

## Usage

After installation:

```bash
# Reload shell
exec zsh

# View all aliases
show-aliases

# Use AI-powered git tasks (from any directory)
task --global commit
task --global create-pr
```

## AI Configuration

The global Taskfile supports multiple AI tools. Configure your preference:

```bash
# Edit the config file
nano ~/.config/task/taskfile.env

# Examples:
AI_COMMAND=kiro-cli chat --no-interactive --agent ci-cd
AI_COMMAND=copilot --agent ci-cd -p
```

Projects can override with local `.taskfile/taskfile.env` if needed.

## Customization

### Per-Project Taskfile
Create a local `Taskfile.yml` in your project to add project-specific tasks or override global ones.

### Local AI Config
Create `.taskfile/taskfile.env` in a project to use a different AI command for that project.

## AI Tools Setup

The `ai/setup.sh` script installs MCP servers for Claude Code, deploys shared AI coding guidelines, and configures AI agent profiles. It can be run standalone or is prompted automatically at the end of `install.sh`.

```bash
bash ai/setup.sh
```

At startup it asks which tools to configure:

```
Which AI tools do you want to set up?
  [1] Claude Code
  [2] Kiro
```

Every step is individually confirmable — answer `n` to skip any step.

### What gets installed

**Claude Code:**
- MCP: Serena — code intelligence and semantic navigation
- MCP: Sequential Thinking — structured multi-step reasoning
- MCP: Context7 — up-to-date library documentation (requires Upstash API key)
- `~/.claude/CLAUDE.md` — AI coding guidelines (backup / append / skip if file exists)

**Kiro:**
- `~/.kiro/steering/general.md` and `language-specific.md` — AI coding guidelines
- `~/.kiro/agents/default.json` and `ci-cd.json` — agent configs with correct `uvx` path

**Both selected:** the guidelines step runs once and installs to all selected targets.

### Verify after install

```bash
# Claude Code
claude mcp list          # → serena, sequential-thinking, context7
cat ~/.claude/CLAUDE.md  # → guidelines content

# Kiro
ls ~/.kiro/steering/     # → general.md, language-specific.md
ls ~/.kiro/agents/       # → default.json, ci-cd.json
cat ~/.kiro/agents/default.json | grep command  # → actual uvx path
```

### Adding a new AI tool

1. Add an entry to the tool selector in `select_tools()` (e.g., `[3] Copilot`)
2. Add a `tool_selected "copilot"` block in the main section that calls `register_step` for each step
3. Implement the step functions following the existing patterns

## Requirements

- macOS (some utilities are macOS-specific)
- ZSH
- [Task](https://taskfile.dev) (auto-installed by installer)
- Docker (for container utilities)
- AWS CLI (for AWS utilities)
- AI tool of choice (Kiro CLI, GitHub Copilot, etc.)

## Updating

```bash
cd ~/workbench
git pull
./install.sh
```

## File Locations

- Scripts: `~/.local/bin/`
- ZSH configs: `~/.config/zsh/config.d/`
- Git config: `~/.gitconfig`
- Global Taskfile: `~/.config/task/Taskfile.yml`
- AI config: `~/.config/task/taskfile.env`
