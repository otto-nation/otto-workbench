# Environment Tools
<!-- AUTO-GENERATED — do not edit directly -->
<!-- Sources: brew/registry.yml  brew/work/*.registry.yml  bin/registry.yml  zsh/registry.yml -->
<!-- Regenerate: bin/generate-tool-context -->

## Brew Tools

### jq
JSON processor for querying, filtering, and transforming JSON data
- **When to use**: Parsing API responses, extracting fields from JSON output, transforming JSON config
- **Usage**: `jq '.field' file.json  |  echo '{}' | jq '.key'`
- **Docs**: https://jqlang.github.io/jq/manual/

### yq
YAML/JSON/TOML processor — like jq but for YAML
- **When to use**: Reading or modifying YAML files (Taskfile.yml, docker-compose.yml, k8s manifests)
- **Usage**: `yq '.key' file.yaml  |  yq -i '.key = "value"' file.yaml`
- **Docs**: https://mikefarah.gitbook.io/yq/

### gh
GitHub CLI — manage PRs, issues, repos, checks, and releases from the terminal
- **When to use**: Creating/viewing PRs, checking CI status, managing issues, cloning repos, viewing runs
- **Usage**: `gh pr create  |  gh pr view  |  gh run list  |  gh issue create`
- **Docs**: https://cli.github.com/manual/

### go-task
Task runner with YAML-defined tasks (used via the 'task' wrapper script)
- **When to use**: Running defined dev workflows: commits, PRs, brew management
- **Usage**: `task commit  |  task pr:create  |  task --global <cmd>`
- **Docs**: https://taskfile.dev

### shellcheck
Static analysis tool for shell scripts — catches bugs and style issues
- **When to use**: Before committing any .sh file; run automatically in pre-push and CI
- **Usage**: `shellcheck script.sh`
- **Docs**: https://www.shellcheck.net/

### bats-core
Bash Automated Testing System — unit testing framework for shell scripts
- **When to use**: Writing or running tests for bash scripts in this repo
- **Usage**: `bats tests/  |  bats tests/foo.bats`
- **Docs**: https://bats-core.readthedocs.io/

### zoxide
Smarter cd — learns frequently-visited directories and jumps to them by partial name
- **When to use**: Navigating to frequently-used directories faster than cd
- **Usage**: `z workbench  |  z src`
- **Docs**: https://github.com/ajeetdsouza/zoxide

### uv
Fast Python package and project manager (Rust-based pip/venv replacement)
- **When to use**: Installing Python tools, managing venvs, running Python scripts without installing
- **Usage**: `uv pip install <pkg>  |  uvx <tool>`
- **Docs**: https://docs.astral.sh/uv/

### pipx
Install and run Python CLI tools in isolated environments
- **When to use**: Installing Python-based CLI tools globally (e.g. Serena, pre-commit)
- **Usage**: `pipx install <tool>  |  pipx run <tool>`
- **Docs**: https://pipx.pypa.io/

### nvm
Node Version Manager — install and switch between Node.js versions
- **When to use**: Switching Node versions per project; installing a specific Node version
- **Usage**: `nvm use <version>  |  nvm install <version>  |  nvm ls`
- **Docs**: https://github.com/nvm-sh/nvm

### colima
Lightweight container runtime for macOS (Docker-compatible, no Docker Desktop needed)
- **When to use**: Running Docker containers locally
- **Usage**: `colima start  |  colima stop  |  colima status`
- **Docs**: https://github.com/abiosoft/colima

### 1password-cli
1Password CLI (op) — access secrets, SSH keys, and vaults from the terminal
- **When to use**: Retrieving secrets in scripts; signing git commits via SSH agent
- **Usage**: `op read 'op://vault/item/field'  |  op run -- <cmd>`
- **Docs**: https://developer.1password.com/docs/cli/

### gitleaks
Secret scanner — detects committed credentials, tokens, and keys
- **When to use**: Automatically on every commit and push via git hooks; run manually to audit history
- **Usage**: `gitleaks protect --staged  |  gitleaks detect`
- **Docs**: https://github.com/gitleaks/gitleaks

### gnupg
GNU Privacy Guard — GPG encryption and signing
- **When to use**: Signing git commits with GPG; encrypting/decrypting files
- **Usage**: `gpg --sign  |  gpg --verify`
- **Docs**: https://gnupg.org/documentation/

## Work Stack Tools

### aws
AWS CLI — manage AWS resources, services, and credentials from the terminal
- **When to use**: Interacting with AWS services (S3, EC2, Lambda, Secrets Manager, etc.); managing IAM and credentials
- **Usage**: `aws s3 ls  |  aws secretsmanager get-secret-value --secret-id <name>  |  aws sts get-caller-identity`
- **Docs**: https://docs.aws.amazon.com/cli/

### aws-sso-util
Utilities for AWS SSO — simplifies login and credential management for SSO-based AWS accounts
- **When to use**: Logging in via AWS SSO; configuring SSO profiles; refreshing SSO credentials
- **Usage**: `aws-sso-util login  |  aws-sso-util configure populate`
- **Docs**: https://github.com/benkehoe/aws-sso-util

### gradle
Gradle build tool — build, test, and publish JVM projects
- **When to use**: Building, testing, or running Gradle-based projects when no wrapper (gradlew) is present
- **Usage**: `gradle build  |  gradle test  |  gradle dependencies`
- **Docs**: https://docs.gradle.org/

### jenv
Java version manager — switch between multiple JDK versions per project
- **When to use**: Switching Java versions globally or per-directory; managing multiple JDKs
- **Usage**: `jenv versions  |  jenv local 17  |  jenv global 21`
- **Docs**: https://www.jenv.be/

### mvn
Apache Maven — build and dependency management for Java projects
- **When to use**: Building, testing, or packaging Maven-based projects
- **Usage**: `mvn clean install  |  mvn test  |  mvn dependency:tree`
- **Docs**: https://maven.apache.org/guides/

### jira
Jira CLI (ankitpokhrel/jira-cli) — manage Jira tickets from the terminal
- **When to use**: Viewing, moving, or commenting on Jira tickets; checking ticket status; assigning issues
- **Usage**: `jira issue view PROJ-123 --plain  |  jira issue list -P PROJ --plain  |  jira issue move PROJ-123 "In Progress"`
- **Docs**: https://github.com/ankitpokhrel/jira-cli

### kubectl
Kubernetes CLI — manage clusters, deployments, pods, and services
- **When to use**: Inspecting or modifying Kubernetes resources; debugging pods; applying manifests
- **Usage**: `kubectl get pods  |  kubectl describe pod <name>  |  kubectl apply -f manifest.yml  |  kubectl logs <pod>`
- **Docs**: https://kubernetes.io/docs/reference/kubectl/

### tfenv
Terraform version manager — install and switch between Terraform versions
- **When to use**: Switching Terraform versions per project; installing a specific Terraform version
- **Usage**: `tfenv list  |  tfenv install 1.7.0  |  tfenv use 1.7.0`
- **Docs**: https://github.com/tfutils/tfenv

### terraform-docs
Generate documentation from Terraform module inputs and outputs
- **When to use**: Auto-generating or updating README documentation for Terraform modules
- **Usage**: `terraform-docs markdown table . > README.md  |  terraform-docs --config .terraform-docs.yml .`
- **Docs**: https://terraform-docs.io/

## Workbench Scripts

### task
AI-powered Git automation runner; wraps go-task with global/local Taskfile routing
- **When to use**: Running any dev workflow — commits, PRs, brew management
- **Usage**: `task commit  |  task pr:create  |  task --global <cmd>`
- **Docs**: https://taskfile.dev

### aliases
Lists all custom shell aliases and functions with optional keyword filtering
- **When to use**: Discovering available aliases; answering 'what shortcut exists for X?'
- **Usage**: `aliases  |  aliases docker  |  aliases git`

### claude-rules
Manages local Claude Code rule additions not tracked in the workbench
- **When to use**: Adding machine-specific or project-specific Claude instructions
- **Usage**: `claude-rules add <domain> "rule"  |  claude-rules list  |  claude-rules status`

### otto-workbench
Re-applies all workbench configuration to ~/ (symlinks, settings, rules)
- **When to use**: After pulling workbench updates or when config gets out of sync
- **Usage**: `otto-workbench sync`

### generate-tool-context
Generates ai/guidelines/rules/tools.generated.md from the domain registries
- **When to use**: After adding/updating any registry.yml entry; runs automatically on pre-push and workbench sync
- **Usage**: `bin/generate-tool-context`

### mem-analyze
macOS memory analysis report — pressure, swap usage, top processes, per-user totals
- **When to use**: Diagnosing memory issues, high swap, or identifying memory-hungry processes
- **Usage**: `mem-analyze`

### get-secret
Interactively retrieves a secret from AWS Secrets Manager by listing and selecting
- **When to use**: Fetching credentials or config values stored in Secrets Manager
- **Usage**: `get-secret`

### cleanup-testcontainers
Stops and removes stale Testcontainers Docker resources left by test runs
- **When to use**: After test runs that left orphaned containers; when freeing Docker resources
- **Usage**: `cleanup-testcontainers`

### generate-git-rules
Regenerates git.generated.md from lib/ai-commit.sh constants
- **When to use**: After changing commit types, header length, or PR conventions in lib/ai-commit.sh
- **Usage**: `bin/generate-git-rules`

### validate-registries
Validates all tool registry YAML files for schema correctness and cross-file consistency
- **When to use**: After adding or editing any registry.yml; runs automatically on pre-push
- **Usage**: `bin/validate-registries`

## Shell Aliases

### Git aliases
Short forms for common git operations (gs, ga, gc, gp, gl, gco, gb, gd)
- **When to use**: Any git workflow
- **Usage**: `gs (status)  ga (add)  gc (commit)  gp (push)  gl (log)  gco (checkout)  gb (branch)  gd (diff)`

### Docker aliases
Short forms for docker and docker compose operations (d, dc, d-ps, d-logs, d-exec, d-clean)
- **When to use**: Managing local Docker containers and images
- **Usage**: `d (docker)  dc (compose)  d-ps (ps)  d-logs (logs -f)  d-exec (exec -it)  d-clean (prune)`

### Kubernetes aliases
Short forms for kubectl operations (k, k-pods, k-svc, k-deploy, k-logs, k-exec, k-ns)
- **When to use**: Managing Kubernetes resources
- **Usage**: `k (kubectl)  k-pods  k-svc  k-deploy  k-logs  k-exec  k-ns <namespace>`

### Development aliases
Shortcuts for Gradle, Node, and Serena (gw, gw-build, gw-test, nom, serena)
- **When to use**: Running Gradle builds/tests, resetting node_modules, launching Serena
- **Usage**: `gw (./gradlew)  gw-build  gw-test  gw-clean  nom (reset node_modules)  serena`

### System aliases
macOS and shell utilities (update, reload, list-ports, ip, flush-dns, afk)
- **When to use**: System maintenance, network diagnostics, shell management
- **Usage**: `update (brew+npm)  reload (restart shell)  list-ports  ip (external IP)  flush-dns  afk (lock screen)`
