# Tools & Scripts Reference

Complete catalog of workbench scripts, installed tools, and shell aliases. Auto-generated from [tool registries](registries.md) — do not edit the generated sections directly.

## Scripts

<!-- SCRIPTS-TABLE-START -->
| Script | Description |
|--------|-------------|
| `task` | AI-powered Git automation runner; wraps go-task with global/local Taskfile routing |
| `otto-workbench` | Manage your workbench developer environment |
| `mem-analyze` | macOS memory analysis report — pressure, swap usage, top processes, per-user totals |
| `wt-cleanup` | Remove stale git worktrees — merged branches and optionally age-based cleanup |
| `resolve-branch` | Resolve a fuzzy branch name to an exact git branch — tries exact, worktree, separator, fuzzy |
| `wt-init` | Convert a regular git repo to a bare repo with worktrees |
| `lint-sweep` | Sweep lint violations across multiple Go repos — detect, report, and optionally create fix branches |
| `validate-nesting` | Validate bash, Python, and Go script nesting depth to enforce flat control flow |
| `gcloud-reauth` | Check GCP application-default credentials and re-login if expired, with self-managed launchd agent |
| `get-secret` | Interactively retrieves a secret from AWS Secrets Manager by listing and selecting |
| `claude-review` | Run Claude's reviewer agent on a PR with local worktree checkout and iterative review support |
| `claude-rules` | Manages local Claude Code rule additions not tracked in the workbench |
| `review-rebuild` | Rebuild review.md from group finding files — recovers from synthesis formatting drift |
| `review-threads` | Thread lifecycle status for PR review comments — dashboard and JSON report |
| `review-thread-triage` | Deprecated — redirects to review-threads --triage |
| `review-orchestrate` | Review orchestration engine for claude-review — manages tier classification, file grouping, and review merging |
| `review-post` | Deterministic posting of review findings to GitHub as a PENDING PR review |
| `validate-review-positions` | Validates review finding positions against a PR diff to ensure comment placement accuracy |
| `build-otto-ai-tools-tarball` | Package otto-ai-tools into a self-contained tarball for distribution |
| `dream-scan` | Scan session transcripts and memory state for dream consolidation |
| `dream-verify` | Verify dream memory file integrity across all projects |
| `promote-scan` | Scan memories and workbench artifacts for promotion evaluation |
| `retro-scan` | Scan PR review comments and cross-reference against coding rules |
| `pr` | Unified PR lifecycle CLI — CI failures, code review, and review comments |
| `otto-log` | Query trail files across otto-workbench AI scripts — structured audit trail of actions, decisions, and errors |
| `pr-rebase` | Rebase current branch onto origin/main with conflict detection and force-push |
| `ci-check` | Fetch CI run data, classify failures, and output status dashboard |
| `ceiling-scan` | Scan for ceiling: markers and produce a structured debt ledger |
| `reuse-mode-tracker` | Track /reuse lite|full|ultra commands via UserPromptSubmit hook |
| `reuse-session-start` | SessionStart hook — inject reuse level and ceiling scan nudge |
| `reuse-subagent-start` | SubagentStart hook — inject reuse level into spawned subagents |
| `workbench-reference` | Reference card — lists all workbench skills, agents, and reuse modes |
| `workbench-statusline` | Status line script — displays reuse level, ceiling debt count, and review status |
| `run-auto-task` | Run a Claude Code skill as a headless background session with output logging |
| `serena-mcp` | Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence |
| `validate-registries` | Validates all tool registry YAML files for schema correctness and cross-file consistency |
| `validate-components` | Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency |
| `validate-migrations` | Validates migration file naming, function naming, and shebang conventions |
| `validate-errexit` | Validates bash scripts for dangerous && patterns that silently exit under set -e |
| `validate-skills` | Validates SKILL.md frontmatter conventions — required fields, name/directory consistency, lifecycle field pairing |
| `validate-cli-flags` | Validates CLI flag conventions — no --repo alias, --pr/--branch mutual exclusivity |
| `generate-tool-context` | Generates tools.generated*.md rule files from the domain registries |
| `cleanup-testcontainers` | Stops and removes stale Testcontainers Docker resources left by test runs |
| `generate-changelog` | Generates a changelog from conventional commits grouped by type |
| `ghostty-terminfo-push` | Installs Ghostty's xterm-ghostty terminfo on a remote host — fixes 'Error opening terminal' over SSH |
| `aliases` | Lists all custom shell aliases and functions with optional keyword filtering |
<!-- SCRIPTS-TABLE-END -->

## Script Reference

Detailed usage for user-facing scripts. Internal scripts (validators, generators, scanners) are listed in the table above but not detailed here.

### `otto-workbench`

Manage your workbench developer environment.

```
otto-workbench [--workbench-dir <path>] <command>
```

| Flag | Description |
|------|-------------|
| `--workbench-dir <path>` | Override workbench root (e.g., a worktree checkout) |

**Commands:**

| Command | Description |
|---------|-------------|
| `install [--all] [COMPONENT ...]` | Bootstrap the workbench (first-time, interactive). `--all` skips menus |
| `sync` | Re-apply all workbench config to `~/` — migrations, symlinks, tool context, AI settings |
| `ai init [--force] [--analyze]` | Scaffold `.claude/` in the current git repo. `--force` re-scaffolds existing, `--analyze` runs `/analyze-project` after |
| `ai sync` | Sync machine-level AI config (settings, rules, MCPs, skills, agents) |
| `ai override <type> [name] [flags]` | Manage user overrides for agents, skills, or rules. Flags: `--add`, `--disable`, `--enable`, `--status` |
| `discover [regenerate]` | Show installed components, scripts, and launchd agents. `regenerate` re-detects components |
| `autoupdate start [SECONDS]` | Start scheduled pull + sync via launchd (default: every 12h) |
| `autoupdate stop` | Stop autoupdate |
| `autoupdate status` | Show autoupdate status |
| `autoupdate run` | Run update now |
| `autoupdate logs` | Show recent log entries |
| `changelog` | Show recent changes from conventional commits |

### `task`

Wrapper around go-task that adds `--global` support.

```
task [--global] <task-name> [-- <task-args>]
```

| Flag | Description |
|------|-------------|
| `--global` | Use the global Taskfile (`~/.config/task/Taskfile.yml`) from any directory |
| `-h`, `--help` | Show help |

Without `--global`, uses `./Taskfile.yml` in the current directory.

### `mem-analyze`

macOS memory analysis report — pressure, swap usage, top processes, per-user totals.

```
mem-analyze
```

| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help |

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `SWAP_WARN_THRESHOLD_MB` | Swap warning threshold in MB | `10240` |
| `PROCESS_WARN_THRESHOLD_KB` | Process memory warning threshold in KB | `500000` |

### `wt-cleanup`

Remove stale git worktrees — merged branches and optionally age-based cleanup.

```
wt-cleanup [--age <days>] [--no-grace-period] [--dry-run] [--quiet]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--age <days>` | Also remove worktrees inactive for N+ days | — |
| `--no-grace-period` | Skip the 600s grace period (remove immediately) | grace period on |
| `--dry-run` | Show what would be removed | — |
| `--quiet` | No output (for hooks) | — |
| `-h`, `--help` | Show help | — |

### `wt-init`

Convert a regular git repo to a bare repo with worktrees.

```
wt-init [--dry-run] [<path>]
```

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview what would happen |
| `-h`, `--help` | Show help |

`<path>` defaults to the current directory.

### `lint-sweep`

Sweep lint violations across multiple Go repos — detect, report, and optionally create fix branches.

```
lint-sweep --rule <name> --repos <glob> [--fix] [--branch <name>] [--dry-run] [--json]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--rule <name>` | Lint rule to sweep (required) | — |
| `--repos <glob>` | Glob or comma-separated list of repo paths (required) | — |
| `--fix` | Create worktrees and branches for fixing | report only |
| `--branch <name>` | Branch name override | `<user>/fix/<rule>` |
| `--dry-run` | Show what would be done | — |
| `--json` | Output results as JSON | — |
| `-h`, `--help` | Show help | — |

### `gcloud-reauth`

Check GCP application-default credentials and re-login if expired, with self-managed launchd agent.

```
gcloud-reauth [<command>]
```

| Command | Description |
|---------|-------------|
| *(none)* | Check credentials, launch login if expired |
| `install` | Install launchd agent (runs every 12h) |
| `uninstall` | Remove launchd agent |
| `status` | Show agent status |
| `-h`, `--help` | Show help |

### `get-secret`

Interactively retrieves a secret from AWS Secrets Manager by listing and selecting.

```
get-secret
```

| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help |

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `AWS_REGION` | Target region | `us-east-1` |
| `AWS_PROFILE` | Credential profile | ambient credential chain |

Outputs the raw SecretString value to stdout.

### `claude-review`

Run Claude's reviewer agent on a PR with local worktree checkout and iterative review support.

```
claude-review [<flags>] <pr_url_or_number>
claude-review gc
claude-review post <pr_url_or_number>
```

| Command | Description |
|---------|-------------|
| `<pr_url_or_number>` | Run review (default) |
| `gc` | Clean up stale review artifacts |
| `post <pr_url_or_number>` | Post an existing review file |

| Flag | Description | Default |
|------|-------------|---------|
| `--no-post` | Skip all interactive prompts; run review and exit | — |
| `--post` | Run review then post automatically (fully headless) | — |
| `--submit` | Submit the review (use with `--post` for fully headless) | — |
| `--self` | Self-review mode: output to `ignore/reviews/self-review.md` | — |
| `--skip-user-verification` | Skip ownership check in self-review mode | — |
| `--force` | Skip pending review and stale review warnings | — |
| `--no-holistic` | Skip holistic phase in multi-phase reviews | — |
| `--json-summary` | Suppress human output; emit JSON summary to stdout | — |
| `--issue <link>` | Attach an issue link for reviewer context | — |
| `--max-parallel <N>` | Max concurrent group reviews | `4` |
| `--max-cost <USD>` | Max total review cost in USD | `20` |
| `--model <name>` | Override model for all agents (e.g., `sonnet`, `opus`) | — |
| `--repo-dir <path>` | Path to local repo or worktree (aliases: `--repo`, `--worktree`) | auto-detected |
| `-V`, `--version` | Show version | — |
| `-h`, `--help` | Show help | — |

`--no-post` and `--post` are mutually exclusive.

### `claude-rules`

Manages local Claude Code rule additions not tracked in the workbench.

```
claude-rules <command> [<args>]
```

| Command | Description |
|---------|-------------|
| `sync` | Re-symlink workbench rules and regenerate `workbench.md` |
| `add <domain> "rule"` | Append a rule to `~/.claude/rules/<domain>.local.md` |
| `list` | List all local rule files with line counts |
| `status` | Show local rules not tracked in workbench |
| `open [domain]` | Open a local rule file in `$EDITOR` |
| `project add "rule"` | Append a convention to the current repo's `CLAUDE.md` |
| `project show` | Display the current repo's `CLAUDE.md` |
| `-V`, `--version` | Show version |
| `-h`, `--help` | Show help |

Domain aliases: `ts`/`js` → `typescript`, `py` → `python`, `sh`/`shell` → `bash`, `yml` → `yaml`.

### `claude-review threads`

Show thread lifecycle status for a PR — dashboard and JSON report.

```
claude-review threads [<pr_url_or_number>] [--resolve-verified] [--repo-dir <PATH>]
```

| Flag | Description |
|------|-------------|
| `<pr_url_or_number>` | PR number or URL (auto-detects from current branch if omitted) |
| `--resolve-verified` | Resolve all verified threads on GitHub |
| `--repo-dir <PATH>` | Git worktree directory (aliases: `--repo`, `--worktree`) |

### `serena-mcp`

Scaffolds Serena MCP into a project's `.mcp.json` for project-scoped code intelligence.

```
serena-mcp <command>
```

| Command | Description |
|---------|-------------|
| `init` | Add Serena to `.mcp.json` in the current directory (creates if missing) |
| `status` | Show whether Serena is configured in the current project |
| `-h`, `--help` | Show help |

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
| [parallel](https://www.gnu.org/software/parallel/) | GNU parallel — run shell commands in parallel (required by bats --jobs for parallel test execution) |
| [tree](https://oldmanprogrammer.net/source.php?dir=projects/tree) | Recursive directory listing tool — visualizes folder structure as a tree |
| [delta](https://dandavison.github.io/delta/) | Syntax-highlighting pager for git diffs — automatically used for all git diff output via core.pager |
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

See [Registries](registries.md#adding-an-entry) for the full schema and step-by-step instructions.
