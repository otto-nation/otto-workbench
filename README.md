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
