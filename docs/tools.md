# Tools & Scripts Reference

Complete catalog of workbench scripts, installed tools, and shell aliases. Auto-generated from [tool registries](architecture.md#tool-registry) — do not edit the generated sections directly.

## Scripts

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
| `generate-tool-context` | Generates ai/guidelines/rules/tools.generated.md from the domain registries |
| `claude-review` | Run Claude's reviewer agent on a PR and optionally post findings to GitHub |
| `claude-rules` | Manages local Claude Code rule additions not tracked in the workbench |
| `serena-mcp` | Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence |
| `cleanup-testcontainers` | Stops and removes stale Testcontainers Docker resources left by test runs |
| `generate-changelog` | Generates a changelog from conventional commits grouped by type |
| `generate-git-rules` | Regenerates git.generated.md from lib/conventions.sh constants |
| `ghostty-terminfo-push` | Installs Ghostty's xterm-ghostty terminfo on a remote host — fixes 'Error opening terminal' over SSH |
| `aliases` | Lists all custom shell aliases and functions with optional keyword filtering |
<!-- SCRIPTS-TABLE-END -->

## Installed Tools

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
| [worktrunk](https://worktrunk.dev) | Git worktree manager — create, switch, list, merge, and remove worktrees with hooks and CI integration |
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

## Adding a Tool

Each tooling directory has a [`registry.yml`](architecture.md#tool-registry) that describes the tools it provides. Add an entry whenever you add a new brew formula, bin script, or alias group:

```yaml
# brew/registry.yml
- name: ripgrep
  description: "Fast regex search tool"
  when_to_use: "Searching file contents; faster alternative to grep"
  docs: https://github.com/BurntSushi/ripgrep
```

Required fields: `name`, `description`, `when_to_use`. Optional: `usage`, `docs`.

After editing a registry, regenerate:

```bash
generate-tool-context
```

The pre-push hook enforces that generated files are always up to date.
