---
title: Tools & Scripts
description: Complete catalog of workbench scripts, installed tools, and shell aliases, generated from the tool registries.
---

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
| `otto-log` | Query the unified trail root and AI usage across otto-workbench scripts — audit trail plus cost and token stats |
| `pr-rebase` | Rebase current branch onto origin/main with conflict detection and force-push |
| `pr-describe` | Revise the PR description against the repo's PR template once the branch stops moving |
| `ci-check` | Fetch CI run data, classify failures, and output status dashboard |
| `ceiling-scan` | Scan for ceiling: and ceiling-permanent: markers and produce a structured debt ledger |
| `reuse-mode-tracker` | Track /reuse lite|full|ultra commands via UserPromptSubmit hook |
| `reuse-session-start` | SessionStart hook — inject reuse level and ceiling scan nudge |
| `reuse-subagent-start` | SubagentStart hook — inject reuse level into spawned subagents |
| `workbench-reference` | Reference card — lists all workbench skills, agents, and reuse modes |
| `workbench-statusline` | Status line script — displays reuse level, ceiling debt count, and review status |
| `run-auto-task` | Run a Claude Code skill as a headless background session with output logging |
| `ai-usage-log` | Bridge shell-invoked AI calls into the global usage ledger — render, unwrap, record |
| `eval-models` | Evaluation runner — scores AI calls against a corpus, one task per manifest |
| `otto-mcp-server` | Dynamic MCP server — discovers tool-schema scripts and exposes them to MCP clients |
| `claude-bash-guard` | PreToolUse hook for the Bash tool — blocks command shapes that trigger unsuppressible permission prompts |
| `serena-mcp` | Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence |
| `validate-all` | Runs every validator discovered in bin/ and bin/local/ — the single entry point used by the pre-push hook and CI |
| `validate-registries` | Validates all tool registry YAML files for schema correctness and cross-file consistency |
| `validate-components` | Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency |
| `validate-migrations` | Validates migration file naming, function naming, and shebang conventions |
| `validate-errexit` | Validates bash scripts for dangerous && patterns that silently exit under set -e |
| `validate-skills` | Validates SKILL.md frontmatter conventions — required fields, name/directory consistency, lifecycle field pairing |
| `validate-cli-flags` | Validates CLI flag conventions — no --repo alias, --pr/--branch mutual exclusivity |
| `validate-worktree-guards` | Validates that ctx.worktree_root is never dereferenced without a guard or require_worktree() |
| `validate-stat-portability` | Validates that stat format flags are confined to the lib/portable.sh helpers |
| `validate-permissions` | Validates that every Bash permission rule can match a command — no literal * inside a :* prefix, no unexpanded ~ |
| `validate-ceiling` | Validates that every ceiling marker names an upgrade trigger or is marked permanent |
| `validate-tool-schema` | Validates that every script claiming the --tool-schema protocol can answer the MCP server's probe |
| `validate-eval-baselines` | Validates eval baseline files for schema correctness and corpus coverage |
| `generate-tool-context` | Generates tools.generated*.md rule files from the domain registries |
| `generate-config-schema` | Generates config.schema.json and the docs key reference from WorkbenchConfig |
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
| `--self` | Self-review mode: output to `~/.local/state/workbench/reviews/<repo>-self-<branch>/review.md` | — |
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

#### What self-review reads

`--self` reviews the worktree, not the remote branch. Everything that differs from the base branch is in scope: unpushed commits, staged and unstaged edits, and untracked files (`.gitignore` still applies). The head SHA and the changed-file list come from `git`, never from GitHub. When the branch already has a PR, its title, body and labels supply context but do not define the diff — the review logs the local head whenever it differs from the PR's.

Re-reviews narrow to what changed since the prior review, and that delta follows the same rule — uncommitted work done since the last `--self` run is picked up. PR mode is unaffected: it reviews the pushed commits only.

#### Model selection

Each pipeline phase resolves its model as **`--model` flag > `CLAUDE_REVIEW_<PHASE>_MODEL` > `CLAUDE_REVIEW_MODEL` > phase default**. The phase names and their defaults live in `PHASES` ([`ai/lib/review_phases.py`](../ai/lib/review_phases.py)) — the env key is derived from each name by convention, so adding a phase needs no change here.

Bare aliases (`sonnet`, `opus`, `haiku`) resolve through `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, and `ANTHROPIC_DEFAULT_HAIKU_MODEL` when those are set; otherwise the alias is passed to the CLI as-is.

#### Vertex AI quota preflight

When the Claude backend is pointed at Vertex AI, the review aborts before spending anything if a model it would use has no provisioned quota in the target project. The env vars are declared in [`ai/lib/vertex.env.yml`](../ai/lib/vertex.env.yml) and scaffolded into `~/.env.local`.

The gate is fail-open: it only stops runs it can prove are misconfigured. It proceeds — with a note — when the CLI is not on Vertex, when project/region are unset, when there are no application-default credentials, when the Service Usage API errors, or when the model is a bare alias the CLI resolves internally. On failure it lists the provisioned models and names the `CLAUDE_REVIEW_<PHASE>_MODEL` keys worth changing. Quota lookups are cached per project/region for 5 minutes in `${WORKBENCH_CACHE_DIR}/vertex-quota/` — see [Libraries — roots.sh](libraries.md#rootssh).

Requires application-default credentials (`gcloud auth application-default login`) with read access to `serviceusage.googleapis.com`. The check is skipped entirely on non-Claude backends (`AI_BACKEND=pi`).

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

### `pr`

Unified PR lifecycle CLI — manages CI, code review, comments, rebasing, and push state.

```
pr [global flags] <command> [flags]
```

| Global flag | Description |
|-------------|-------------|
| `--repo-dir PATH` | Git worktree directory (auto-detected from CWD when omitted) |
| `--branch NAME` | Branch name override |
| `--pr NUM\|URL` | PR number or URL |

**Commands:**

| Command | Description |
|---------|-------------|
| `status` | Show unified dashboard: CI, review, comments, rebase, and push state |
| `ci [--fix]` | Fetch and classify CI failures; `--fix` attempts automated repair |
| `review [--self] [--fix] [--post] [--repair] [--summary]` | Run code review via `claude-review` |
| `comments [--triage] [--fix] [--finish] [--track THREAD_ID] [--track-all] [--post] [--reply <id> --body-file <path> --post]` | Fetch and manage PR review threads (see phases below); `--post` publishes (default: drafts) |
| `fix` | Run fix passes for CI, review, and comments in one step, then revise the description |
| `rebase [--fix] [--push] [--abort]` | Rebase onto `origin/main` |
| `describe [--force] [--dry-run]` | Revise the PR description against the repo's PR template |
| `gc` | Clean up stale PR review artifacts and cached state |

**`pr comments` flags fall on two axes — phase and gate:**

The phase flags (`--triage`, `--fix`, `--finish`, `--reply`) choose which work
the run does. `--post` is the gate: it decides whether that work leaves the
machine. Every phase drafts to stderr and publishes nothing without it, so
`--post` neither implies a phase nor is implied by one. `--finish --post` is
therefore not saying the same thing twice — the first names the work, the
second opens the gate. The same `--post` gates `pr review` and `--reply`; it is
one switch for the whole process, not a `comments` flag — see
`ai/lib/publishing.py`, which owns it.

The work itself runs in two phases:

`--fix` triages threads, applies mechanical fixes, and resolves the verified
ones. It withholds the summary comment whenever threads need human input,
because the summary is meant to describe a finished conversation.

`--finish` closes out what `--fix` held back: replies on threads whose commit
had not yet been pushed, a tracking issue for the threads named by `--track`,
and the summary comment. It is a second invocation on purpose — the discussion
has to happen in between. ⚠️ Combining them (`--fix --finish`) works and closes
out that run's deferred set, but posts a summary nobody has replied to yet.

`--track THREAD_ID` is repeatable and selects which deferred threads get filed
on the tracking issue; `--track-all` selects every one and overrides any
`--track` ids passed alongside it. Neither is implied by `--finish`. A thread is
deferred because the fix pass ran out of budget, not because anyone decided to
postpone it, and filing it posts a reply under the PR author's name saying a
reviewer's finding was triaged and postponed — so the selection is the user's,
per thread. A `--finish` logs the deferral ids it left unfiled, whether the
selection was empty or partial. Naming an id that is not a deferred thread is an
error rather than a silent skip, so a typo cannot pass for agreement.

**Replies are one per thread:**

Every reply — generated by `--fix`/`--finish` or hand-written via `--reply` —
edits our standing reply in place while that reply is still the last comment on
the thread, and posts a new one only once a reviewer has answered. Editing under
a reviewer's reply would rewrite the text they were responding to; leaving a
second comment when nobody has answered leaves them holding two of our positions
with no way to tell which stands. Whether the thread is resolved makes no
difference: `--finish --post` resolves the threads it answers, and the reply it
left there is still the one to revise.

`--reply <id> --body-file <path> --post` accepts a thread node ID, any comment
`databaseId` in the thread, or a `...#discussion_r<id>` URL, and warns when the
body carries no `blob/<sha>/` permalink to back its claims. Pass `-` as the path
to read the body from stdin. Like every other write here it needs `--post`;
without it the body is printed under `DRAFT (not published)` and nothing is
sent. Use it instead of `gh api .../replies`, which bypasses the dedup entirely.

**The summary is one comment, and it only grows:**

A review cycle runs several rounds against a single `Review Comments Addressed`
comment, edited in place. It reports all five thread outcomes — fixed, already
addressed, dismissed, deferred, and the ones still awaiting discussion — so a
`needs_human` thread appears as open rather than not at all.

The replacement body is built from local state, which is per-target and
per-worktree, and routinely absent for a round the comment already covers: `pr
gc`, a recreated worktree, a later round run from another machine. So the
published comment is read before the edit and any row this run cannot account
for is carried forward verbatim, counted as `N carried over`, and logged. An
edit never removes a row the comment already had.

**`pr describe` is commit-aware:**

The pass records the HEAD it described. A repeated run against an unchanged
branch is a no-op rather than another AI call, which is what lets `pr fix` call
it unconditionally at the end of every run. `--force` ignores the recorded SHA;
`--dry-run` prints the revision instead of applying it.

The template is read from the first of `.github/pull_request_template.md`,
`.github/PULL_REQUEST_TEMPLATE.md`, `pull_request_template.md`, or
`PULL_REQUEST_TEMPLATE.md`. Repos with no file at any of these four paths silently
get the built-in fallback (Summary / Changes / Testing only); a differently-named
template file is not detected.

**Push status in `pr status`:**

`pr status` detects unpushed commits by comparing local HEAD against `origin/<branch>`.
The Push verdict appears in the dashboard and gates the **Merge readiness** line:

| Push state | Dashboard line | Effect on merge readiness |
|------------|----------------|--------------------------|
| Branch not pushed | `**Push**: branch not pushed to remote` | Blocks: "branch not pushed" |
| Commits ahead | `**Push**: N commit(s) not pushed` | Blocks: "N unpushed commit(s)" |
| Up to date | `**Push**: up to date` | No block |

**The undelivered closeout shows up too:**

A `--fix` run that ends with open `needs_human` threads, or that runs without
`--post`, holds back the summary comment and the per-thread replies and records
that in state. A tracking issue that was owed for the deferred threads and could
not be filed — no tracker configured, a provider that cannot create issues, or a
creation that failed — is recorded the same way. `pr status` reads those flags
back out, so the debt is visible after the stderr line has scrolled past:

```
**Fix**: 11 fixed · 2 need discussion · 1 dismissed · 3 already addressed (commit: 9f2c1ab, push_held)
  ⚠ closeout owed: summary + 15 replies — run: pr comments --finish --post

**Merge readiness**: blocked — closeout not delivered (run: pr comments --finish --post)
```

The reply count is derived from the recorded outcomes — the fixed,
already-addressed, and dismissed threads `--finish` drains. A queue that still
owes replies but carries no outcomes to count says `replies` without a number
rather than claiming zero. An unfiled tracking issue reads as
`deferred tracking issue` in the same line.

A draft run owes nothing: the publishing gate declining a write is the gate
working, so it neither posts an error to the trail nor counts against merge
readiness.

### `otto-mcp-server`

Dynamic MCP server. Discovers workbench scripts and exposes them to any MCP client over
stdio. Registered in `~/.claude.json` as `otto-workbench` by `otto-workbench ai sync`.

```
otto-mcp-server
```

A script is discovered when it is executable, its name starts with neither `.` nor `_`,
and it answers `--tool-schema` with JSON carrying at least `name` and `input_schema`.
Scripts built on `ToolParser`
([`ai/lib/tool_parser.py`](../ai/lib/tool_parser.py)) inherit the flag for free.

**Where it looks.** The workbench's own script directories, always. They are derived from
the component layout rather than listed — the root `bin/`, plus every `<component>/bin`
and `<component>/<sub>/bin` in the checkout. That is the same two-level glob
[`lib/components.sh`](../lib/components.sh) uses for `steps.sh` and `migrations`, so a new
component tier such as `editors/zed/bin/` is scanned the moment it exists, with no config
to write. Note it scans the checkout, not the `~/.local/bin` those scripts are symlinked
into: discovery probes a candidate by running it, and `~/.local/bin` also holds everything
else you have installed.

To reach anything outside the workbench, add `~/.config/workbench/mcp-tools.json`, which
is optional:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `tool_dirs` | list of paths | `[]` | Directories scanned **in addition** to the workbench's own |
| `plugin_dirs` | list of paths | `[]` | Directories of `*.json` files, each naming one `tool_dir` |

Both keys add to the derived set rather than replacing it, so an empty list and an absent
key mean the same thing. The workbench's own directories are always scanned.

```json
{
  "tool_dirs": ["~/work/project/bin"],
  "plugin_dirs": ["~/.config/workbench/mcp-plugins"]
}
```

A plugin file points at a directory to scan, letting a project register its tools
without editing the config:

```json
{ "name": "my-project", "tool_dir": "~/work/project/tools" }
```

Discovery reads each candidate's source before running it, and only executes the ones
carrying a protocol marker — a script that ignores unknown flags would otherwise do its
real work when probed. Scripts that mention the flag in prose without implementing it
must word around the literal to stay out of the probe path.

Carrying a marker is a claim to be a tool, so a candidate that then fails to answer — a
non-zero exit, malformed JSON, a schema missing `name` or `input_schema`, or a probe that
outruns the timeout — is logged at warning level on stderr with the reason. A tool you
added that never appears in an MCP client is explained there. Executables with no marker
are not tools and are skipped without comment.

That stderr belongs to whichever MCP client spawned the server, so the same claim is
checked at build time by `bin/local/validate-tool-schema`. It imports this discovery —
the directories, the candidate filter, and the probe itself — and fails when a candidate
in the checkout cannot answer, rather than leaving the tool to vanish at runtime.

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
| [linear](https://github.com/schpet/linear-cli) | Linear CLI (schpet/linear-cli) — manage Linear issues from the terminal |
| [mas](https://github.com/mas-cli/mas) | Mac App Store CLI — search, install, and update App Store apps from the terminal |
<!-- TOOLS-TABLE-END -->

## Adding a Tool

See [Registries](registries.md#adding-an-entry) for the full schema and step-by-step instructions.
