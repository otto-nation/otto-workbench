# Dotfiles

Personal shell configuration and utilities.

## Contents

- **bin/** - Executable scripts (added to PATH)
  - `cleanup-testcontainers` - Clean up Docker testcontainers
  - `get-secret` - Interactive AWS Secrets Manager retrieval
  - `mem-analyze` - System memory analysis report
  - `show-aliases` - Display all configured aliases and functions

- **zsh/** - ZSH configuration files
  - `aliases-docker.zsh` - Docker and container aliases
  - `aliases-kubernetes.zsh` - Kubernetes aliases
  - `aliases-development.zsh` - Git, Gradle, and development tools
  - `aliases-aws.zsh` - AWS utilities
  - `aliases-system.zsh` - System utilities and macOS helpers

- **git/** - Git configuration
  - `.gitconfig` - Git aliases and settings

## Installation

```bash
git clone https://github.com/isaacgarza/dotfiles ~/dotfiles
cd ~/dotfiles
./install.sh
```

The install script will:
1. Symlink scripts to `~/.local/bin/`
2. Symlink zsh configs to `~/.config/zsh/config.d/`
3. Symlink gitconfig to `~/.gitconfig`
4. Add `~/.local/bin` to your PATH

## Usage

After installation, reload your shell:
```bash
exec zsh
```

View all aliases:
```bash
show-aliases
```

## Alias Naming Convention

- **Docker:** `d-*` (e.g., `d-ps`, `d-logs`, `d-exec`)
- **Kubernetes:** `k-*` (e.g., `k-pods`, `k-logs`, `k-exec`)
- **Gradle:** `gw-*` (e.g., `gw-build`, `gw-test`)
- **Git:** Short forms (`gs`, `ga`, `gc`) + explicit (`git-status`, `git-add`)

## Requirements

- ZSH
- macOS (some utilities are macOS-specific)
- Docker (for container utilities)
- AWS CLI (for AWS utilities)

## AI Configuration (Not Included)

AI coding guidelines and Taskfiles are **project-specific** and not included in this dotfiles repo.

### Kiro CLI Guidelines

For consistent AI behavior, create guidelines in your projects:

```bash
# Project-specific (recommended)
my-project/.kiro/steering/general.md

# User-level defaults
~/.kiro/steering/general.md
```

See: https://gist.github.com/isaacgarza (ai-coding-guidelines)

### Taskfile

For git automation, add `Taskfile.yml` per project:

```bash
my-project/Taskfile.yml
```

See: https://gist.github.com/isaacgarza (Taskfile.yml)

## Updating

To get the latest changes:

```bash
cd ~/dotfiles
git pull
./install.sh  # Re-run to update symlinks
```

## Updating

To get the latest changes:

```bash
cd ~/dotfiles
git pull
./install.sh  # Re-run to update symlinks
```

## New Machine Setup

```bash
# 1. Clone dotfiles
git clone https://github.com/isaacgarza/dotfiles ~/dotfiles

# 2. Install
cd ~/dotfiles
./install.sh

# 3. Reload shell
exec zsh
```

## Related Resources (Not Included)

Some configurations are intentionally **not included** because they're project-specific:

### AI Coding Guidelines
- **Location:** Project-specific (`.kiro/steering/`) or user-level (`~/.kiro/steering/`)
- **Why not included:** Guidelines vary per project and are still evolving
- **See:** https://gist.github.com/isaacgarza (ai-coding-guidelines)

### Taskfile
- **Location:** Project-specific (`Taskfile.yml` in project root)
- **Why not included:** Tasks are unique to each project
- **See:** https://gist.github.com/isaacgarza (Taskfile.yml)

These are kept separate to avoid conflicts and maintain flexibility across different projects and machines.

## Updating

To get the latest changes:

```bash
cd ~/dotfiles
git pull
./install.sh  # Re-run to update symlinks
```

## New Machine Setup

```bash
# 1. Clone dotfiles
git clone https://github.com/isaacgarza/dotfiles ~/dotfiles

# 2. Install
cd ~/dotfiles
./install.sh

# 3. Reload shell
exec zsh
```
