---
title: Tools & Scripts
description: Complete catalog of workbench scripts, installed tools, and shell aliases, generated from the tool registries.
---
<!-- Generated from docs/tools.src.md by bin/local/compose-docs — do not edit. -->

# Tools & Scripts Reference

Complete catalog of workbench scripts, installed tools, and shell aliases. Generated from the [tool registries](registries.md) and from the scripts themselves — nothing on this page is written here.

## Scripts

| Script | Description |
|--------|-------------|
| `record-filed-issue` | Records an issue filed by hand in the branch's follow-up ledger — called by both harnesses' post-execution hooks |
| `pr` | Unified PR lifecycle CLI — creation, CI failures, code review, and review comments |
| `review` | Run the configured review agent on a PR with local worktree checkout and iterative review support |
| `otto-log` | Query the unified trail root and AI usage across otto-workbench scripts — audit trail plus cost and token stats |
| `workbench-rules` | Manages this machine's own coding-rule layers — local additions and overrides, for whichever harnesses are installed |
| `ceiling-scan` | Scan for ceiling: and ceiling-permanent: markers and produce a structured debt ledger |
| `dream-scan` | Scan session transcripts and memory state for dream consolidation |
| `dream-verify` | Verify dream memory file integrity across all projects |
| `promote-scan` | Scan memories and workbench artifacts for promotion evaluation |
| `retro-scan` | Scan PR review comments and cross-reference against coding rules |
| `retro-consume` | Delete the local reviews a retro consumed, for the scan ID that recorded them |
| `wiki` | Mechanical operations over a compiled knowledge base — status, lint, signals, archive, source hashes, index, browsing link, snapshots |
| `workbench-reference` | Reference card — lists all workbench skills, agents, and reuse modes |
| `ci-check` | Fetch CI run data, classify failures, and output status dashboard |
| `pr-rebase` | Rebase current branch onto its base with conflict detection and force-push |
| `pr-describe` | Revise the PR description against the repo's PR template once the branch stops moving |
| `review-orchestrate` | Review orchestration engine for review — manages tier classification, file grouping, and review merging |
| `review-post` | Deterministic posting of review findings to GitHub as a PENDING PR review |
| `review-rebuild` | Rebuild review.md from group finding files — recovers from synthesis formatting drift |
| `review-threads` | Thread lifecycle status for PR review comments — dashboard and JSON report |
| `validate-review-positions` | Validates review finding positions against a PR diff to ensure comment placement accuracy |
| `ai-usage-log` | Bridge shell-invoked AI calls into the global usage ledger — render, unwrap, record |
| `eval-models` | Evaluation runner — scores AI calls against a corpus, one task per manifest |
| `rules-canary` | Canary for the --add-dir coupling — fails when coding rules stop reaching an agent |
| `otto-mcp-server` | MCP server — offers the tools the component registries declare, with schemas imported in-process |
| `build-otto-ai-tools-tarball` | Package otto-ai-tools into a self-contained tarball for distribution |
| `build-claude-config-tarball` | Package Claude Code configuration into a tarball for server or container deployment |
| `workbench-export` | Export workbench Claude configs as a self-contained tarball, filtered by profile |
| `task` | AI-powered Git automation runner; wraps go-task with global/local Taskfile routing |
| `otto-workbench` | Manage your workbench developer environment |
| `mem-analyze` | macOS memory analysis report — pressure, swap usage, top processes, per-user totals |
| `wt-cleanup` | Remove stale git worktrees — merged branches and optionally age-based cleanup |
| `resolve-branch` | Resolve a fuzzy branch name to an exact git branch — tries exact, worktree, separator, fuzzy |
| `resolve-workspace` | Print the workspace directory holding a repository's plans and specs — <container>/workspace, outside every checkout |
| `resolve-worktree` | Print the worktree a bare-repo container stands in for — the checkout of its default branch |
| `wt-init` | Convert a regular git repo to a bare repo with worktrees |
| `lint-sweep` | Sweep lint violations across multiple Go repos — detect, report, and optionally create fix branches |
| `validate-nesting` | Validate bash, Python, and Go script nesting depth to enforce flat control flow |
| `gcloud-reauth` | Check GCP application-default credentials and re-login if expired, with self-managed launchd agent |
| `get-secret` | Interactively retrieves a secret from AWS Secrets Manager by listing and selecting |
| `claude-bash-guard` | PreToolUse hook for the Bash tool — blocks command shapes that trigger unsuppressible permission prompts |
| `claude-edit-guard` | PreToolUse hook for Edit/Write — blocks edits on main/master unless gitignored, and edits to a tree a validator holds |
| `claude-session-lock` | Session hooks that record this session as editing its worktree — what stops an unattended fix pass committing over it |
| `reuse-mode-tracker` | Track /reuse lite|full|ultra commands via UserPromptSubmit hook |
| `reuse-session-start` | SessionStart hook — inject reuse level and ceiling scan nudge |
| `reuse-subagent-start` | SubagentStart hook — inject reuse level into spawned subagents |
| `workbench-statusline` | Status line script — displays reuse level, ceiling debt count, and review status |
| `run-auto-task` | Run a Claude Code skill as a headless background session with output logging |
| `serena-mcp` | Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence |
| `run-tests` | Runs the bats and pytest suites with the repo's parallelism settings — the single entry point used by the Taskfile, the pre-push hook, and CI |
| `validate-all` | Runs every validator discovered in bin/ and bin/local/ — the single entry point used by the pre-push hook and CI |
| `with-tree-lock` | Declares a tree under validation while a command runs — the lock that tells editors a gate is in flight |
| `with-session-lock` | Records that an interactive agent session is editing a worktree — the lock that stops an unattended fix pass committing over it |
| `validate-registries` | Validates all tool registry YAML files for schema correctness and cross-file consistency |
| `validate-components` | Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency |
| `validate-migrations` | Validates migration file naming, function naming, and shebang conventions |
| `validate-errexit` | Validates bash scripts for dangerous && patterns that silently exit under set -e |
| `validate-skills` | Validates SKILL.md frontmatter conventions — required fields, name/directory consistency, lifecycle field pairing, agent-to-skill coverage |
| `validate-cli-flags` | Validates CLI flag conventions — no --repo alias, --pr/--branch mutual exclusivity |
| `validate-worktree-guards` | Validates that ctx.worktree_root is never dereferenced without a guard or require_worktree() |
| `validate-timeouts` | Validates that every subprocess timeout comes from the ai/lib/core/timeouts.py tiers |
| `validate-magic-values` | Validates that an abbreviated sha, an exit code, and a truncation cap are spelled by the module that owns them |
| `validate-frozen-roots` | Validates that no module freezes a workbench root into an import-time constant |
| `validate-ai-layers` | Validates that every ai/lib package imports only what the layer declaration in its __init__.py permits |
| `validate-import-form` | Validates that every ai/lib module is imported as `import pkg.mod` and named through its package |
| `validate-stat-portability` | Validates that stat format flags are confined to the lib/portable.sh helpers |
| `validate-bats-version` | Validates that a bats suite using flags on run declares bats_require_minimum_version 1.5.0 |
| `validate-pi-extension-clones` | Validates that every pi extension package clone on this machine can serve the installed pi, in both skew directions and against the pi-ai floor each package declares — a clone predating pi 0.86's transcript contract silently strips the agent's tools and lets it fabricate their output |
| `validate-tmpdir-isolation` | Validates that a bats suite pins $TMPDIR to bats-owned scratch and never removes it by hand, so parallel cases do not share one directory and a failed setup cannot wipe the real temp directory |
| `validate-script-loading` | Validates that only tests/conftest.py executes a module out of a file, so one script never has two module objects |
| `validate-skip-coverage` | Validates that a platform skip does not silence a test whose subject is a tracked file — CI is Linux-only, so a darwin guard over a repo file is an assertion that never runs |
| `validate-permissions` | Validates that every Bash permission rule can match a command, that no untracked settings file duplicates a tracked grant or re-grants a gated one, and that a tracked allow bucket is in the codepoint order both ai sync and Claude Code write it back in — --fix prunes the duplicates and sorts the bucket |
| `validate-ceiling` | Validates that every ceiling marker names an upgrade trigger or is marked permanent |
| `validate-file-size` | Fails when a source file passes 600 code lines — blanks, comments and docstrings are not counted, so documenting a file never pushes it over. The files already over are named with the issue that splits each |
| `validate-test-layout` | Fails when a Python module under tests/ is neither <subject>_test.py nor a declared support module, or when a Python or bats suite passes the 600-code-line cap the source gate uses. The bats suites already over are named with the issue that splits them |
| `check-new-tests` | Runs the tests a change adds against a worktree at the merge base and reports any that pass without the change — the revert check the testing rule prescribes, done once from the diff |
| `validate-yq-version` | Fails when this machine's yq is older than the one CI pins — an expression the older parser rejects fails every registry read at once |
| `validate-eval-baselines` | Validates eval baseline files for schema correctness and corpus coverage |
| `validate-eval-floors` | Holds committed eval baselines to the high-water floors in eval/results/floors.json |
| `validate-docs-composed` | Validates that every composed doc matches what its docs/*.src.md composes to |
| `validate-doc-reference` | Validates that a source doc renders every module group its source set declares |
| `validate-doc-budget` | Validates that a doc declaring a line budget stays within it and holds no '####' heading |
| `validate-tracked-ignored` | Validates that no tracked file lives under a path .gitignore claims to ignore |
| `validate-rules` | Validates rule frontmatter conventions — harness scoping resolves, no Claude-only tool vocabulary in a rule that reaches Pi |
| `compose-docs` | Composes docs/*.md from docs/*.src.md by expanding include directives into generator output |
| `generate-doc-reference` | Renders a module reference from the doc blocks of a source set's own modules |
| `generate-cli-reference` | Renders a registered tool's usage line or flag tables from its own argparse parser |
| `generate-tool-context` | Generates tools.generated*.md rule files from the domain registries |
| `generate-config-schema` | Generates config.schema.json and the docs key reference from WorkbenchConfig |
| `generate-public-surface` | Generates the per-package public surface snapshot from the registries, config schema, and shipped artifacts |
| `validate-public-surface` | Validates that the committed public surface snapshots match the registries, config schema, and shipped artifacts they are generated from |
| `generate-test-weights` | Writes tests/weights.tsv from a bats junit report — the measured per-file runtimes the CI shard packer balances on |
| `select-tests` | Emits the bats test files affected by a set of changed paths — used by the pre-push hook for change-based selection |
| `select-pytest` | Emits the pytest files affected by a set of changed paths, resolved through the import graph — the pytest counterpart of select-tests |
| `claim-job-slots` | Holds a share of the machine's test-parallelism slots while a command runs, so concurrent suites in several worktrees divide the cores instead of each taking all of them |
| `suite-watch` | Supervises a test-suite child and writes a periodic stderr heartbeat of elapsed time and in-flight work, so a slow run is distinguishable from a stuck one |
| `validate-test-deps` | Validates that every bats test has resolvable source refs or is in the always-run list |
| `validate-pytest-deps` | Validates that every pytest file resolves deps through the import graph or is declared unmappable |
| `check-surface-compat` | Fails when a public surface entry is removed without a breaking-change or Not-Breaking declaration |
| `cleanup-testcontainers` | Stops and removes stale Testcontainers Docker resources left by test runs |
| `generate-changelog` | Generates a changelog from conventional commits grouped by type |
| `wt-fetch-default` | Brings the local default branch up to date with origin's — the worktrunk pre-switch hook |
| `ghostty-terminfo-push` | Installs Ghostty's xterm-ghostty terminfo on a remote host — fixes 'Error opening terminal' over SSH |
| `aliases` | Lists all custom shell aliases and functions with optional keyword filtering |

## Script Reference

Each section below is the script's own header — the comment block under its shebang, or a Python script's docstring — so the description lives beside the code it describes and changes with it. Which scripts appear is the registries' decision: every `full` or `brief` tool is here unless its entry says `reference: false`, and a `hidden` one only when it says `reference: true` (see [Registries](registries.md#tool-entries)). The workbench's own validators and generators in `bin/local/` are documented in [CONTRIBUTING.md](https://github.com/otto-nation/otto-workbench/blob/main/CONTRIBUTING.md) instead.

How the AI subsystem behaves behind these entry points — review phases, publishing, settlement, the summary record — is in [AI Automation](ai-automation.md), and each module's own account is in [AI Libraries](ai-libraries.md).

**Workbench scripts** — general-purpose scripts installed onto `PATH` by `otto-workbench sync`.

### `gcloud-reauth`

Check GCP application-default credentials and re-login if expired, with a
self-managed launchd agent.

With no command, prints whether credentials are valid and, if not, launches
`gcloud auth application-default login`. `gcloud` is resolved via mise when
that install exists, otherwise from `PATH`.

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

Interactively retrieve a secret from AWS Secrets Manager by listing and selecting.

Lists all secrets in the configured region, prompts for selection by number,
then prints the raw `SecretString` value to stdout.

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

### `lint-sweep`

Sweep lint violations across multiple Go repos — detect, report, and optionally
create fix branches.

Detects violations of a given golangci-lint rule across a set of repos (paths
that contain `go.mod`) and reports a per-repo count. With `--fix`, creates a
worktree per repo that has violations (requires `wt`) on branch
`<user>/fix/<rule>` unless `--branch` overrides it.

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

Requires `golangci-lint`. Repos that are not Go modules are skipped; matching
nothing is an error.

### `mem-analyze`

macOS memory analysis report — pressure, swap usage, top processes, per-user totals.

Prints a formatted report with system information (model, installed RAM),
memory pressure (normal / warning / critical), a page breakdown from `vm_stat`,
swap usage, the top 10 processes by memory, every process above the process
warning threshold, per-user totals, and a summary with recommendations based
on pressure plus swap state.

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

macOS-only — requires: `system_profiler`, `memory_pressure`, `vm_stat`, `sysctl`, `bc`, `perl`.

### `otto-workbench`

Manage your workbench developer environment.

```
otto-workbench [--workbench-dir <path>] <command>
```

| Flag | Description |
|------|-------------|
| `--workbench-dir <path>` | Override workbench root (e.g. a worktree checkout) |

The subcommands, rendered from `bin/registry.yml`'s `commands:` list rather
than restated here:

| Command | Scope | Description |
|---------|-------|-------------|
| `otto-workbench install [--all] [COMPONENT ...]` | All components | Bootstrap the workbench on a new machine (first-time setup) |
| `otto-workbench sync` | All components | Re-apply all workbench config — migrations, symlinks, tool context, AI settings |
| `otto-workbench discover` | Environment overview | Show installed components, available scripts, and agent status |
| `otto-workbench discover regenerate` | Component state | Re-detect installed components after manual changes |
| `otto-workbench ai init` | Project | Scaffold .claude/ in the current repo with stack-detected rules |
| `otto-workbench ai init --force` | Project | Re-scaffold an existing project's .claude/ directory |
| `otto-workbench ai sync` | Machine | Sync machine-level AI config (settings, rules, skills, agents, MCPs) |
| `otto-workbench ai override` | Machine | Manage user overrides for AI agents, skills, and rules |
| `otto-workbench changelog` | Git history | Show recent changes from conventional commits |
| `otto-workbench projects` | Machine | List the repos on this machine that use the workbench |
| `otto-workbench projects add [DIR]` | Machine | Register a repo that hasn't run a workbench command yet |
| `otto-workbench projects forget DIR` | Machine | Drop a repo's entry from the registry |
| `otto-workbench projects prune` | Machine | Drop registry entries whose directory is gone |
| `otto-workbench permissions sweep` | Machine | Report Claude Code permission-grant drift in every registered repo |
| `otto-workbench permissions sweep --prune` | Machine | Delete the local grants another rule already makes |
| `otto-workbench permissions mirror` | Machine | Copy a repo's tracked grants into the bare-repo container above its worktrees |
| `otto-workbench config set KEY VALUE` | Machine | Write one key into the machine-wide config, checked against the key surface |
| `otto-workbench config set KEY VALUE --project` | Project | Write one key into the current repo's .workbench.yml instead |
| `otto-workbench config set KEY VALUE --container` | Container | Write one key into the .workbench.yml above a bare repo's worktrees |
| `otto-workbench config get KEY [DIR ...]` | Project | Resolve one key for this repo, or for each named one, with the scope that answered |
| `otto-workbench config status` | Project | Show every scope, every resolved value, and the file each came from |

`install` runs first-time setup: installs Homebrew (if missing), syncs core
components (bin, git, task, zsh), presents a menu of optional components
(brew packages, docker, terminals, editors, ai, mise), and runs pending
migrations. Safe to re-run — idempotent. Use `--all` to skip menus, or name
specific components.

`ai init` scaffolds a `.claude/` directory in the current git repo (if one
does not exist) with stack-detected rules and a project anatomy file. Use
`--force` to re-scaffold, `--analyze` to run `/analyze-project` after.
`ai sync` syncs machine-level AI config (settings, rules, skills, agents,
MCPs). `ai override` manages user overrides.

`sync` is pure config reconciliation: re-symlinks scripts, `zsh/`,
`git/.gitconfig`, Taskfile, `lib/`; syncs Claude `settings.json`, `CLAUDE.md`,
`rules/`, `skills/`, `agents/`, MCPs; merges Zed/Sublime editor settings;
and reconciles the Ghostty theme key. Git and tool-context rule files
regenerate during `sync_ai`.

`sync` does not do one-time setup — that is `otto-workbench install`: Homebrew
packages or casks, Docker runtime, iTerm themes, Ghostty config from template.

### `resolve-branch`

Resolve a fuzzy branch name to an exact git branch — tries exact, worktree,
separator, then fuzzy.

```
resolve-branch <input>
```

| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help |

Resolution cascade (stops at the first match):

1. Exact match against local and remote branches (`origin/<input>` included)
2. Worktree directory basename match (including a worktree mid-rebase)
3. Separator normalization (`-` → `/` for the first two segments)
4. Case-insensitive fuzzy search

The resolved name is printed on stdout with a `remotes/origin/` prefix stripped.
Several fuzzy hits are listed on stderr rather than guessed.

| Exit | Meaning |
|------|---------|
| `0` | Resolved; branch name on stdout |
| `1` | No match, several fuzzy hits, or missing `<input>` |

### `resolve-workspace`

Print the workspace directory holding a repository's plans and specs —
`<container>/workspace`, outside every checkout.

A plan or a spec is about the repository, not about one branch of it. Written
inside a worktree it is duplicated across every sibling checkout, invisible
from the others, and destroyed by `wt remove` — so it goes at the container,
the directory holding the bare `.git` with every worktree as a peer. Nothing
there is inside any checkout, which is also why it needs no `.gitignore` entry.

```
resolve-workspace [<path>]
```

| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help |

`<path>` defaults to the current directory.

| Exit | Meaning |
|------|---------|
| `0` | Resolved; the workspace path is on stdout |
| `1` | An ordinary clone, which has no container to write into, or a directory that is not in a git repository |
| `64` | Usage error |

Exit `1` is a refusal and not a fallback. Writing to `<repo>/workspace` in an
ordinary clone would put the artifact back inside the checkout, which is the
arrangement this exists to end; convert the repo with `wt-init` instead.

The single owner of this path. Both halves of the answer are borrowed rather
than re-derived: `lib/git_layout.sh`'s `git_shared_dir` names the shared git
dir, whose parent is the container, and `bin/resolve-worktree` decides whether
that parent really is one.
`bin/migrations/20260922-rename-issues-jira-url-container.sh` composes the
same two for the same reason. `lib/git_layout.py`'s
`container_dir` is the Python spelling of the pair and returns None exactly
where this exits `1`; `tests/resolve_workspace.bats` holds them to one answer.

### `resolve-worktree`

Print the worktree a bare-repo container stands in for — the checkout of its
default branch.

A bare repo has no working tree, so anything rooted at the container sees none
of the repo's tracked files — no `CLAUDE.md`, no `.claude/`, no source. This
prints the worktree checked out on the repo's default branch: the tree such a
tool should read and write instead.

```
resolve-worktree [<path>]
```

| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help |

`<path>` defaults to the current directory. The default branch is the one the
container's own `HEAD` names — git guarantees that ref exists, it needs no
remote, and it is what `git clone` reads to pick the branch a new checkout
lands on. `refs/remotes/origin/HEAD` is deliberately not consulted: `git clone
--bare` creates no remote-tracking refs, so reading it would mean guessing
between `master` and `main` for most containers.

| Exit | Meaning |
|------|---------|
| `0` | Resolved; the worktree path is on stdout |
| `1` | A bare repo, but no worktree holds the default branch (or its `HEAD` is detached) |
| `2` | Not a bare repository — nothing to resolve |
| `64` | Usage error |

Exit `2` is the ordinary answer for an everyday repo, a worktree, or a
directory outside any repo, so callers treat it as "carry on here" rather than
a failure. This is the one owner of that resolution in bash. The `claude` and
`pi` shell wrappers reach it through
[`_worktree_launch.zsh`](architecture.md#shell-zsh) — redirecting a launch is
all they want. A tool that resolves a tree in order to *write* a project
artifact into it calls [`lib/worktree.sh`](libraries.md)'s `project_root`
instead, which lets a working tree name itself first and falls back to this
only when there is none; the ceiling-debt Stop hook, `serena-mcp`,
`workbench-rules`, and `otto-workbench ai init` all reach it that way.

[`lib/permission_mirror.py`](libraries.md) applies the same rule in Python to
pick the worktree a container's permission mirror is copied from. The two must
agree — a session redirected to a worktree the mirror never wrote from is a
session missing the grants the mirror exists to deliver, with nothing to say
so — and `tests/container_source.bats` fails if they diverge.

### `task`

Wrapper around go-task that adds `--global` support. Installed to
`~/.local/bin/task`, which takes precedence over the real binary; the wrapper
finds the real `task` by dropping that directory from `PATH`.

```
task [--global] <task-name> [-- <task-args>]
```

| Flag | Description |
|------|-------------|
| `--global` | Use the global Taskfile (`~/.config/task/Taskfile.yml`) from any directory |
| `-h`, `--help` | Show help |

Without `--global`, uses a Taskfile in the current directory (`Taskfile.yml` /
`Taskfile.yaml`, either case) and passes through to the real binary unchanged.
Missing both a local Taskfile and `--global` is an error.

### `validate-nesting`

Validate bash, Python, and Go script nesting depth to enforce flat control flow.

Each language's checker in `lib/nesting/` owns its default max depth as
`DEFAULT_MAX_DEPTH`; `--max-depth` overrides all of them. The numbers are
deliberately not restated here — this text once said Go was 3 while every
checker had long since settled on 2.

```
validate-nesting [--quiet] [--max-depth N] [--diff BASE_REF] [file...]
```

When no files are given, discovers all scripts in the repo by extension and
shebang. `--diff` is mutually exclusive with positional files. The flag table
below the exit codes is rendered from the parser, so it is not written here.

| Exit | Meaning |
|------|---------|
| `0` | All files pass |
| `1` | Any file exceeds max depth |
| `2` | Usage error (`--diff` with positional files, or `BASE_REF` did not resolve) |

**Flags**

| Flag | Description |
|------|-------------|
| `--quiet` | Only show failures and summary. |
| `--max-depth` `<n>` | Maximum nesting depth (overrides per-language defaults). |
| `--diff` `<base-ref>` | Only check violations in lines added since BASE_REF. |
| `[<file> ...]` | Files to check (auto-discovers if omitted). |

### `wt-cleanup`

Remove stale git worktrees — merged branches and optionally age-based cleanup.

By default, removes worktrees whose branches are fully merged (safe to delete),
and deletes the branch with the worktree — including a squash merge, which git's
own ancestry check cannot see. With `--age <days>`, also removes worktrees with
no commits newer than that many days; those branches are kept, since inactivity
says nothing about whether the work landed.

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

A merged worktree holding uncommitted changes is reported rather than removed,
and the question of what counts as a change is asked of the default branch rather
than of the worktree's own index. The branch is already in the default branch, so
the default branch's ignore rules are the ones that decide whether a file is worth
preserving — and a worktree cut before a rule landed does not carry it, which is
the only reason git reports the file at all. A file whose lines merely moved is
forgiven on the same grounds: a reordering of a branch that already landed is
residue, not work. The same check applies to an `--age` removal of an unmerged
worktree — disposable residue there does not hold it back either.

Nothing else is forgiven. A file the default branch does not ignore, one that
gained or lost a line, a staged change, a rename, and a deletion all still hold
the worktree back and are named in the summary. Every path the check forgives is
written to `~/.local/state/workbench/logs/wt-cleanup.log` with its reason, so a
removal this widens can be audited afterwards.

The worktree list comes from `wt list --format json`, whose payload carries a
`schema` number. The script reads schema 2 and refuses any other with an error
rather than reading fields that may have moved — a mismatch means worktrunk
changed the format and `wt-cleanup` needs updating for it. This is deliberately
noisy: the bump to schema 2 moved every field the script read, and because the
call site swallowed the failure it no-opped silently on every session exit
instead of reporting anything.

### `wt-init`

Convert a regular git repo to a bare repo with worktrees.

The current branch's working tree (including uncommitted changes) is moved
into a named worktree directory inside the bare repo root. Existing worktrees
(e.g. Claude agent worktrees in `.claude/worktrees/`) are preserved in place
and relocated to the bare root. Requires `wt` (worktrunk).

```
wt-init [--dry-run] [<path>]
```

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview what would happen |
| `-h`, `--help` | Show help |

`<path>` defaults to the current directory. Must be run from the repo root
(after `cd` into `<path>`). A detached HEAD is refused — checkout a branch
first. Safe to re-run: a repo that is already bare is skipped, with an offer
to create a missing default-branch worktree.

**AI tooling** — the `pr` and `review` CLIs, the scanners the workbench skills drive, and the MCP launchers.

### `ceiling-scan`

Scan for `ceiling:` / `ceiling-permanent:` markers and produce a structured debt ledger.

Called by the `ceiling-debt` skill (`ai/skills/ceiling-debt`). Walks a tree,
skips binaries, oversized files, and minified lines, and reports every marker
that opens its own comment line.

```
ceiling-scan [--summary-only] [--json] [--output PATH] [DIR]
```

Two forms: `ceiling:` is a simplification that should end at a named condition;
`ceiling-permanent:` is accepted for good and counted apart. The trigger is an
explicit "Upgrade trigger:" sentence, or the first clause turning on if / once /
when / unless / until. A `ceiling:` marker with no such clause is **no-trigger**
(what `bin/local/validate-ceiling` rejects).

Default output is a markdown ledger grouped by file. `--summary-only` prints one
count line. `--json` prints `{"total": N, "no_trigger": M, "permanent": P}`
(counts only, not the markers). `--output PATH` writes the ledger and removes
the file when no markers remain.

Exit code is always 0 (informational), including when DIR is not a directory.

**Flags**

| Flag | Description |
|------|-------------|
| `[<dir>]` | Directory to scan (default: current directory). |
| `--summary-only` | Output only the summary line. |
| `--json` | Output counts as JSON: {"total": N, "no_trigger": M, "permanent": P}. |
| `--output` `<path>` | Write ledger to file instead of stdout; removes the file when no markers remain. |
| `-V`, `--version` | show program's version number and exit. |

### `dream-scan`

Scan session transcripts and memory state for dream consolidation.

Replaces Phases 1+2 of the `dream` skill (`ai/skills/dream`): Orient + Gather
Signal. The `architecture` skill also uses the path-discovery flags rather than
globbing a harness tree.

Sessions come from `core.sessions`, which owns what a session is and where one
lives across every harness. The report header prints a per-harness transcript
count, so a harness that has stopped being discovered reads as a zero.

```
dream-scan [--days N] [--home DIR]
dream-scan [--days N] [--home DIR] --list-transcripts
dream-scan --memory-dir REPO
```

Default stdout is a markdown report: an HTML comment carrying the trail root
(`<!-- scan-id: ... -->`), a Sessions Scanned header, Memory State (per
registered repo), and Session Signals grouped by category (correction,
preference, decision, pattern, review_feedback).

`--list-transcripts` prints every transcript path in the window, one per line,
and exits. `--memory-dir REPO` prints that repo's memory directory and exits.

**Flags**

| Flag | Description |
|------|-------------|
| `-V`, `--version` | print version and exit. |
| `--days` `<n>` | scan sessions from last N days (default: 7). |
| `--home` `<dir>` | home directory override (for testing; default: `$HOME`). |
| `--list-transcripts` | print transcript paths for the window, one per line, and exit. |
| `--memory-dir` `<repo>` | print the memory directory for the repo at REPO, and exit. |

### `dream-verify`

Verify dream memory file integrity across all projects.

Walks every repo memory directory under `$WORKBENCH_MEMORY_DIR` that contains
a `MEMORY.md` and checks:

1. `MEMORY.md` is at most 200 lines
2. Every `(filename.md)` reference in `MEMORY.md` resolves to a file
3. Topic files contain no relative dates (`yesterday`, `last week`, …)
4. No duplicate `name:` frontmatter across topic files

```
dream-verify
```

| Flag | Description |
|------|-------------|
| `-V`, `--version` | Show version |
| `-h`, `--help` | Show help |

| Exit | Meaning |
|------|---------|
| `0` | All checks pass, or no memory directories found |
| `1` | One or more checks fail |

### `otto-log`

Query the unified trail root and AI usage across otto-workbench scripts.

Trails are monthly files under the state root (`workbench_paths.trail_dir()`).
`stats` reads a separate monthly-rotated AI usage ledger that every AI call
appends to.

One user command spans several processes, each with its own `invocation` and all
sharing a `root`. `show` takes any of those IDs and renders the whole command;
`--only` narrows it to the named process. `list` rows are whole commands for the
same reason — `pr review` is one row rather than three.

A time window selects *commands*, not events: a command whose first event
predates the window is listed whole when any part of it falls inside.

```
otto-log recent [--since 1h] [--repo org/repo]
otto-log query [--script NAME] [--level L] [--pr N] [--root ID] [--since WINDOW]
otto-log show <invocation> [--only]
otto-log list [--script NAME] [--since WINDOW] [--repo org/repo]
otto-log record --script NAME --action KEY [--detail TEXT] [--level info|warn|error]
otto-log prune [--keep N]
otto-log stats [--since 7d] [--by script|task|model|day|phase]
```

`--json` on `recent`, `query`, `show`, and `list` emits JSONL. On `stats` it
emits one JSON object per group (`group`, `calls`, `cost`, token fields,
`median_duration_ms`; plus turn percentiles when `--by phase`).

`record` writes one event for callers that cannot open a `Trail` themselves
(shell close-out scripts, agents between a scan and its close). It inherits
`WORKBENCH_TRAIL_ROOT` so a record written inside a larger command is filed
under that command, and prints the invocation ID.

`prune` drops trail months older than `--keep` (default: the same horizon every
trail already sweeps as it opens).

What the ledger records, and what each stats column means, is on `agent/usage.py`
in docs/ai-libraries.md.

**`otto-log recent`** — Recent events (default: last 1h)

| Flag | Description |
|------|-------------|
| `--since` `<since>` | Time window (e.g. 2h, 1d, 30m). Default: `1h`. |
| `--repo` `<repo>` | Filter by repo (org/repo). |
| `--json` | Output raw JSONL. |

**`otto-log query`** — Filter events

| Flag | Description |
|------|-------------|
| `--script` `<script>` | Filter by script name. |
| `--level` `<level>` | Filter by level (debug, info, warn, error). |
| `--event-type` `<event-type>` | Filter by event type. |
| `--invocation` `<invocation>` | Filter by invocation ID (one process). |
| `--root` `<root>` | Filter by root invocation ID (one whole user command). |
| `--pr` `<pr>` | Filter by PR number. |
| `--repo` `<repo>` | Filter by repo (org/repo). |
| `--since` `<since>` | Time window (e.g. 2h, 1d). |
| `--json` | Output raw JSONL. |

**`otto-log show`** — Show one command's timeline

| Flag | Description |
|------|-------------|
| `<invocation>` | Invocation ID — the command's own, or any process under it. |
| `--only` | Just the named process, not the whole command it belongs to. |
| `--json` | Output raw JSONL. |

**`otto-log list`** — List invocations

| Flag | Description |
|------|-------------|
| `--script` `<script>` | Filter by script name — lists the whole command that reached it. |
| `--since` `<since>` | Time window (e.g. 2h, 1d) — selects commands active in it, each listed whole even if it started earlier. |
| `--repo` `<repo>` | Filter by repo (org/repo). |
| `--json` | Output raw JSONL. |

**`otto-log record`** — Write one event to the trail (for shell and agent callers)

| Flag | Description |
|------|-------------|
| `--script` `<script>` | Name the event is filed under. Required. |
| `--action` `<action>` | What happened, as a short key. Required. |
| `--detail` `<detail>` | One line of prose about it. |
| `--level` `<info\|warn\|error>` | Severity (default: info). |
| `--data` `<key=value>` | Structured field, repeatable — numeric values are stored as numbers. |
| `--repo` `<repo>` | Subject repo (org/repo), recorded as context. |
| `--pr` `<pr>` | Subject PR number, recorded as context. |

**`otto-log prune`** — Drop trail months past the horizon

| Flag | Description |
|------|-------------|
| `--keep` `<keep>` | Months of history to keep (default: 6). |

**`otto-log stats`** — Aggregate AI cost and token usage

| Flag | Description |
|------|-------------|
| `--since` `<since>` | Time window (e.g. 24h, 7d). Default: `7d`. |
| `--by` `<script\|task\|model\|day\|phase>` | Group rows by. Default: `script`. |
| `--json` | Output one JSON object per group. |

### `otto-mcp-server`

MCP server launcher. Offers the tools the component registries declare, over
stdio. Registered in `~/.claude.json` as `otto-workbench` by
`otto-workbench ai sync`.

```
otto-mcp-server
```

Discovery reads the component registries — the same files that document every
workbench script — rather than globbing `bin/` directories or probing
executables. `ai/lib/config/tool_registry.py` maps each registered script's
path to its entry; the Python server (`ai/claude/mcps/server.py`) reads that
mapping.

There is no configuration file. The server hosts the workbench's own tools, so
what to offer is a fact about the checkout's registries. Adding a tool means
registering it in a component's `registry.yml`.

The launcher runs `uv run --no-project --with mcp`. A client spawns the server
with its own project as the working directory, and without `--no-project` uv
would resolve and install that project first — writing a virtualenv and a lock
file into somebody else's checkout, and failing outright where the project
does not build. The server needs `mcp` and nothing from wherever it was
launched.

Requires: `uv`, the `mcp` Python package.

### `pr`

Unified PR lifecycle CLI — creation, CI, code review, comments, rebasing, and push state.

`pr [global flags] <command> [flags]`. The global flags work in any position and
name which worktree, branch, or PR a command acts on; omit them and all three
are resolved from the current directory. `pr <command> --help` prints that
command's own flags, and `--tool-schema` prints a JSON document describing the
tool (or, after a command, that command) and exits.

The flag tables below are rendered from the parsers `pr` parses with. How each
command behaves — phases, review modes, comment settlement, rebase conflict
handling — is in [AI Automation](ai-automation.md), and each module's own account
is in [AI Libraries](ai-libraries.md).

**Global flags**

| Flag | Description |
|------|-------------|
| `--repo-dir`, `--worktree` `<path>` | Git worktree to act on; detected from the current directory when omitted. |
| `--branch` `<name>` | Branch to act on, resolved to the worktree it is checked out in. |
| `--pr` `<num\|url>` | PR number or URL to act on. |
| `--schema-version` `<n>` | Serve a versioned JSON document on stdout instead of a human table, where the command has a contract. |

**`pr create`** — Create a PR, or preview it with --dry-run

| Flag | Description |
|------|-------------|
| `--draft` | Open the PR as a draft. |
| `--no-verify` | Skip the pre-push hook when pushing the branch. |
| `--dry-run` | Print the title and body; push nothing, create nothing. |
| `--base` `<branch>` | Branch the PR targets (default: the repo's default branch). |
| `--title` `<text>` | Use this title instead of generating one. |
| `--body` `<text>` | Use this body instead of generating one. Not with `--body-file`. |
| `--body-file` `<path>` | Read the body from this file instead of generating one. Not with `--body`. |
| `--issue` `<id>` | Issue to give the description context about; closes nothing. |
| `--closes` `<id>` | Issue to close on merge (repeatable). |

**`pr status`** — Show CI, review, and comment status dashboard

Takes no flags.

**`pr ci`** — Check CI failures

| Flag | Description |
|------|-------------|
| `--run` `<id>` | Specific run ID. |
| `--fix` | Invoke AI to fix failures after diagnosis. |
| `--post` | Push the fixes; without it the push is drafted. |
| `--wait` | Poll until all jobs complete, emitting incremental reports. |
| `--wait-timeout` `<sec>` | Max wait time in seconds (default: 900). |
| `--wait-interval` `<sec>` | Poll interval in seconds (default: 30). |

**`pr review`** — Run code review

| Flag | Description |
|------|-------------|
| `--no-post` | Do not post the review to GitHub. |
| `--submit` | Submit the GitHub review after posting (default: leave PENDING). |
| `--self` | Review a local checkout of the current branch, or of the branch or PR ref given. |
| `--fix` | Apply findings after the review (requires --self). |
| `--push` | Push the --fix commit (requires --fix). |
| `--skip-user-verification` | Skip the PR-ownership check when --self is given a PR ref. |
| `--force` | Skip stale-review and pending-review prompts; not with --recover. |
| `--no-holistic` | Skip the holistic scan phase. |
| `--no-scout` | Skip the scout phase. |
| `--no-group` | Skip the group review phase. |
| `--no-synthesis` | Skip the synthesis phase. |
| `--no-disprove` | Skip the disprove gate phase. |
| `--disprove` | Enable the disprove-it gate (default: effort-based). |
| `--json-summary` | Print a machine-readable summary on stdout. |
| `--issue` `<url>` | Related issue to include in the review prompt. |
| `--base`, `--onto` `<base>` | Branch to review against, as a bare name. Default: the PR's base, else the branch this one is stacked on, else the repo's default branch. |
| `--max-parallel` `<n>` | Max concurrent group reviews (default: from the machine slot pool, cap 4). |
| `--max-cost` `<usd>` | Max total review cost in USD. |
| `--model` `<name>` | Override the model for all agents (e.g. sonnet, opus). |
| `--effort` `<low\|medium\|high>` | Effort preset (default: review.effort in config.yml, else medium). |
| `--max-groups` `<n>` | Max file groups in multi-phase reviews (default: effort-based). |
| `--generated` | Include tier3-generated files (skipped by default). |
| `-V`, `--version` | Print version and exit. |
| `[<pr\|branch> ...]` | PR number, URL, or branch name. |
| `--post` | Post an existing review to GitHub — a mode; excludes the other mode flags. |
| `--repair` | Repair broken review artifacts via summary or rebuild — a mode; excludes the other mode flags. |
| `--summary` | Print a JSON summary of an existing review — a mode; excludes the other mode flags. |
| `--recover` | Finish a review whose agents failed, at the commit it started from — a mode; excludes the other mode flags. |
| `--list` | List every review in the user's state root — a mode; excludes the other mode flags. |

**`pr comments`** — Fetch and manage PR review threads

| Flag | Description |
|------|-------------|
| `--triage` | Phase: classify threads via AI and auto-resolve verified. |
| `--fix` | Phase: triage threads and apply mechanical fixes via Claude agent. |
| `--no-verify` | Skip the verify gate after --fix. The gate runs the project's own checks against each claimed fix and demotes the ones that do not hold up; without it every fix publishes as unverified. |
| `--finish` | Phase: close out deferred work — replies, tracking issue, summary. Drafts them unless --post is given. |
| `--track` `<thread-id>` | File this deferred thread on the tracking issue (repeatable). Deferral is a per-thread decision, so --finish files nothing unless told which threads. |
| `--track-all` | File every deferred thread. Only for a set the user has actually reviewed. |
| `--post` | Gate, not a phase: publish whatever the chosen phase produced — replies, summaries, resolutions (default: print drafts to stderr and post nothing). |
| `--reply` `<thread-or-comment-id>` | Reply to one thread, editing our standing reply if it is still the last comment. Accepts a thread node ID, a comment ID, or a #discussion_r... URL. A write like any other: needs --post to leave the machine. |
| `--body-file` `<path>` | File holding the --reply body ('-' for stdin). |
| `--settle` `<thread-id>` | Phase: record that you settled this thread by hand (repeatable). Writes local state and nothing else; --finish then replies, resolves and reports it like any other settled thread. |
| `--as` `<fixed\|dismissed\|already_addressed>` | What --settle records (default: fixed). |
| `--reason` `<text>` | Why, for --settle --as dismissed — it becomes the reply the reviewer reads. |
| `--commit` `<sha>` | The commit carrying a --settle --as fixed change, for a fix that landed away from the line the thread is anchored to (default: inferred from that line). Applies to every --settle in the run, so a batch where only some threads need it takes two runs. |

**`pr fix`** — Fix CI + review + comments

| Flag | Description |
|------|-------------|
| `--post` | Publish what the passes produce, the revised PR description included (default: print drafts and post nothing). |
| `[<review-or-ci-flag> ...]` | Any other flag is forwarded to the review pass and the CI pass; see `pr review` and `pr ci`. |

**`pr rebase`** — Rebase onto the branch's base

| Flag | Description |
|------|-------------|
| `--onto`, `--base` `<ref>` | Ref to rebase onto — overrides the PR's base branch and the repo's default branch. |
| `--fork-point` `<ref>` | Replay only the commits after REF, onto the target — for a branch whose earlier commits already landed. The partially-landed refusal names the ref to pass. |
| `--fix` | Autonomous mode — resolve conflicts with AI and rebase (force-pushes unless --no-push). |
| `--no-push` | Skip the force-push — print the command instead. |
| `--push-only` | Push HEAD with the lease an earlier --no-push run recorded; do not rebase. |
| `--no-verify` | Skip the pre-push hook on the force-push. For a hook failure already understood — a flake, or one the branch did not cause. |
| `--force` | Rebase even when the branch's work already landed on the target ref. |
| `--abort` | Abort in-progress rebase. |

**`pr describe`** — Revise the PR description

| Flag | Description |
|------|-------------|
| `--force` | Revise even when HEAD has not moved since the last pass. |
| `--dry-run` | Print the revision instead of applying it. |
| `--post` | Apply the revision to the PR; without it the edit is drafted. |

**`pr batch plan`** — Show which PRs need which steps

| Flag | Description |
|------|-------------|
| `--checkout` `<dir>` | A checkout whose open PRs to plan for; repeatable. Required. |

**`pr batch run`** — Start a run

| Flag | Description |
|------|-------------|
| `--checkout` `<dir>` | A checkout whose open PRs to run on; repeatable. Not with `--plan`. |
| `--plan` `<file>` | A saved `pr batch plan` document. Not with `--checkout`. |
| `--steps` `<steps>` | Comma-separated steps to run (default: rebase,comments,review). |
| `--pool` `<pool>` | Concurrency ceiling. |
| `--auto-publish` `<steps>` | Comma-separated steps whose results publish without asking. |
| `--prs` `<prs>` | Comma-separated repo#number keys to include. |
| `--select` `<key=steps>` | Run exactly these steps for one PR. Repeatable. |

**`pr batch resume`** — Continue a run

| Flag | Description |
|------|-------------|
| `[<run-id>]` | Run to continue (default: the latest). |

**`pr batch resolve`** — Answer one decision

| Flag | Description |
|------|-------------|
| `<run-id>` | Run the decision belongs to. |
| `<decision-id>` | Decision to answer. |
| `--action` `<action>` | The answer; which actions apply depends on the decision's kind. Required. |
| `--reason` `<reason>` | Why, recorded with the answer. |
| `--body-file` `<body-file>` | File holding a reply body, for actions that post one. |
| `--commit` `<commit>` | Commit that settles the decision, for actions that cite one. |

**`pr batch cancel`** — Stop starting new steps

| Flag | Description |
|------|-------------|
| `[<run-id>]` | Run to cancel (default: the latest). |
| `--kill` | Also terminate running steps. |

**`pr batch status`** — Print a run's state

| Flag | Description |
|------|-------------|
| `[<run-id>]` | Run to show (default: the latest). |

**`pr gc`** — Clean up stale PR artifacts

Takes no flags.

### `promote-scan`

Scan memories and workbench artifacts for promotion evaluation.

Replaces Phase 1 of the `promote` skill (`ai/skills/promote`): Orient. Reads
every registered repo's memory plus the workbench checkout's rules, scripts,
hooks, and agents.

```
promote-scan [--home DIR] [--workbench DIR]
```

`--workbench` defaults to `$OTTO_WORKBENCH`, else the checkout this runs from.

Stdout is a markdown report with Memory State (topic files, last-promote stamp),
Backed-Up Memories (`ai/memory` in the workbench), and Workbench Artifacts
(rules, scripts, hooks, agents). Topic bodies are truncated to a preview.

**Flags**

| Flag | Description |
|------|-------------|
| `-V`, `--version` | print version and exit. |
| `--home` `<dir>` | home directory override (for testing; default: `$HOME`). |
| `--workbench` `<dir>` | workbench directory (default: $OTTO_WORKBENCH, else the checkout this runs from). |

### `retro-consume`

Delete the local reviews a retro consumed, if the record answers to it.

Phase 4 of the `retro` skill (`ai/skills/retro`). `retro-scan --consume` recorded
which reviews it read and stamped the record with its scan ID; this presents that
ID back and deletes only what that scan claimed, and only where the review on
disk is still the one it read.

```
retro-consume --scan-id ID [--dry-run]
```

A record that names a different scan is refused (exit 1) rather than honoured.
No consume record is not an error — nothing to clean up. `--dry-run` reports
targets and deletes nothing. The record is cleared only after the deletions it
authorised have happened, so a crash mid-run can be retried with the same ID.
`--scan-id` is required (exit 2 if omitted).

**Flags**

| Flag | Description |
|------|-------------|
| `-V`, `--version` | print version and exit. |
| `--scan-id` `<id>` | the scan ID retro-scan --consume reported. |
| `--dry-run` | report what would be deleted, delete nothing. |

### `retro-scan`

Scan PR review comments and cross-reference them against coding rules.

Replaces Phase 1 of the `retro` skill (`ai/skills/retro`): Orient. Resolves
GitHub remotes from the machine profile's Project Registry, fetches merged-PR
review comments since the last retro (or `--since`), scores each against the
workbench rule set, and folds in local self-review files (deduped against GitHub).

```
retro-scan [--home DIR] [--workbench DIR] [--since DURATION]
retro-scan --consume
```

Stdout is a markdown report of comments grouped by repo/PR, nearest-rule matches,
unmatched comments, and themes (matched comments grouped by rule file).
`--consume` also writes a consume record of the local reviews it read, stamped
with this run's trail root, and prints `<!-- scan-id: ID | consumed: N -->` after
the report so the skill can quote the ID into `retro-consume`.

`--consume` cannot be combined with `--since`. Exit 1 if no registered project
resolves to a GitHub repo — the scan refuses to bank a window over local reviews
alone.

**Flags**

| Flag | Description |
|------|-------------|
| `-V`, `--version` | print version and exit. |
| `--home` `<dir>` | home directory override (default: `$HOME`). |
| `--workbench` `<dir>` | workbench directory (default: $OTTO_WORKBENCH, else the checkout this runs from). |
| `--since` `<duration>` | override scan window (e.g. 7d, 24h, 30m). |
| `--consume` | record the local reviews read, so the retro may delete them when it completes. |

### `review`

Run the configured review agent on a PR with local worktree checkout and iterative review support.

```
review [<flags>] <pr_url_or_number>
review --self [<pr_url_or_number>]
review [--self] --recover [<pr_url_or_number>]
```

`--self` reviews the local worktree (unpushed commits, staged and unstaged
edits, untracked files) rather than the remote branch. `--recover` finishes a
review whose agents failed, at the commit the run started from.

The `gc`, `post`, `rebuild`, `summary` and `threads` subcommands this binary
once carried have moved to `pr` — `pr gc`, `pr review --post`, `pr review
--repair`, `pr review --summary` and `pr comments` respectively. Each is still
recognised here and refused with the command to use instead, rather than being
read as the name of a branch to review.

`--no-post` and `--post` are mutually exclusive. Pipeline phases, base-branch
resolution, model selection, and Vertex quota preflight are in
[AI Automation](ai-automation.md) and the modules in [AI Libraries](ai-libraries.md).

**Flags**

| Flag | Description |
|------|-------------|
| `--no-post` | Do not post the review to GitHub. |
| `--post` | Post the review to GitHub when it finishes. |
| `--submit` | Submit the GitHub review after posting (default: leave PENDING). |
| `--self` | Review a local checkout of the current branch, or of the branch or PR ref given. |
| `--fix` | Apply findings after the review (requires --self). |
| `--push` | Push the --fix commit (requires --fix). |
| `--skip-user-verification` | Skip the PR-ownership check when --self is given a PR ref. |
| `--force` | Skip stale-review and pending-review prompts; not with --recover. |
| `--recover` | Finish a review whose agents failed, at the commit it started from. |
| `--no-holistic` | Skip the holistic scan phase. |
| `--no-scout` | Skip the scout phase. |
| `--no-group` | Skip the group review phase. |
| `--no-synthesis` | Skip the synthesis phase. |
| `--no-disprove` | Skip the disprove gate phase. |
| `--disprove` | Enable the disprove-it gate (default: effort-based). |
| `--json-summary` | Print a machine-readable summary on stdout. |
| `--issue` `<url>` | Related issue to include in the review prompt. |
| `--base`, `--onto` `<base>` | Branch to review against, as a bare name. Default: the PR's base, else the branch this one is stacked on, else the repo's default branch. |
| `--max-parallel` `<n>` | Max concurrent group reviews (default: from the machine slot pool, cap 4). |
| `--max-cost` `<usd>` | Max total review cost in USD. |
| `--model` `<name>` | Override the model for all agents (e.g. sonnet, opus). |
| `--effort` `<low\|medium\|high>` | Effort preset (default: review.effort in config.yml, else medium). |
| `--max-groups` `<n>` | Max file groups in multi-phase reviews (default: effort-based). |
| `--generated` | Include tier3-generated files (skipped by default). |
| `--repo-dir`, `--worktree` `<path>` | Git worktree directory. |
| `--branch` `<name>` | Branch to review (injected by the pr dispatcher). |
| `--pr` `<num\|url>` | PR number or URL (injected by the pr dispatcher). |
| `-V`, `--version` | Print version and exit. |
| `[<pr\|branch> ...]` | PR number, URL, or branch name. |

### `serena-mcp`

Scaffold Serena MCP into a project's `.mcp.json` for project-scoped code
intelligence.

```
serena-mcp <command>
```

| Command | Description |
|---------|-------------|
| `init` | Add Serena to `.mcp.json` in the current project (creates if missing) |
| `status` | Show whether Serena is configured in the current project |
| `-h`, `--help` | Show help |

`.mcp.json` and `.gitignore` are both tracked project files, so the project is
the working tree the current directory belongs to rather than the directory
itself. A shell sitting in a bare-repo container has no working tree, and
`resolve-worktree` names the one the container stands in for; a container whose
default branch has no checkout is an error rather than a write into the
container, where nothing would read the file and no `.gitignore` rule, review,
or CI check could reach it. A directory outside any repository is left alone —
scaffolding there is a real thing to want.

### `wiki`

Mechanical operations over a compiled knowledge base.

The `wiki` skill (`ai/skills/wiki`) owns compilation, querying, and judgement.
This CLI owns only what is decidable without reading for meaning: link graphs,
word counts, file hashes, dates.

```
wiki init    [--vault|--in-repo|--wiki DIR] [--domain TEXT] [--audience TEXT] [DIR]
wiki path    [--wiki DIR] [DIR]
wiki status  [--wiki DIR] [--json] [DIR]
wiki lint    [--wiki DIR] [--json] [--signals] [DIR]
wiki signals [--wiki DIR] [--json] [DIR]
wiki sources [--wiki DIR] [--new] [--json] [DIR]
wiki index   [--wiki DIR] [--check] [DIR]
wiki backup  [--list|--restore NAME] [--keep N] [--wiki DIR] [DIR]
wiki link    [--wiki DIR] [DIR]
wiki ingest  --stage SRC [--type T] [--title TEXT] [--wiki DIR] [DIR]
wiki archive SLUG [--force] [--wiki DIR] [DIR]
```

A knowledge base is a directory holding SCHEMA.md, an `articles/` tree of
compiled markdown, and a `raw/` tree of the immutable sources they were compiled
from. `_index.md`, `_sources.md`, and `_log.md` are generated bookkeeping.

It lives in one of two places, and `init` asks which when a repo has not said:
in the repo (committed and shared), or in this machine's vault (private, outside
every worktree, one folder per repo). The vault is found through `wiki.root`, so
it reads the same from every worktree of a repo and survives the worktree being
removed.

`status`, `lint`, `signals`, and `sources` accept `--json`. `lint` and
`index --check` exit 1 when they have findings. Exit 2 means the knowledge base
could not be located — or, from `init` alone, no location was chosen.

**Global flags**

| Flag | Description |
|------|-------------|
| `-V`, `--version` | show program's version number and exit. |

**`wiki init`** — Create a knowledge base

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--domain` `<domain>` | What the knowledge base is about. |
| `--audience` `<audience>` | Who will use it. |
| `--vault` | Keep it in the machine-level vault, private to this machine. Not with `--in-repo`. |
| `--in-repo` | Keep it in the repo, committed and shared with whoever clones it. Not with `--vault`. |

**`wiki path`** — Print the resolved knowledge base directory

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |

**`wiki status`** — Counts, uncompiled sources, and recent activity

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--json` | Emit JSON. |

**`wiki lint`** — Mechanical health checks over articles and sources

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--json` | Emit JSON. |
| `--signals` | Also report the counted signals. |

**`wiki signals`** — Counted evidence for the judgements lint leaves open

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--json` | Emit JSON. |

**`wiki sources`** — Raw sources with their hashes and compile state

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--json` | Emit JSON. |
| `--new` | Only new or changed sources. |

**`wiki index`** — Rebuild the master index from article frontmatter

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--check` | Report staleness without writing. |

**`wiki backup`** — Snapshot the knowledge base, list snapshots, or restore one

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--list` | List snapshots instead of making one. |
| `--restore` `<name>` | Extract a snapshot beside the base; NAME or 'latest'. |
| `--keep` `<keep>` | How many snapshots to keep (default: 10). |

**`wiki link`** — Create or remove the browsing symlink from this repo to its base

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |

**`wiki ingest`** — Copy a source into raw/ with frontmatter and a real hash

| Flag | Description |
|------|-------------|
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--stage` `<src>` | File to copy into raw/. Required. |
| `--type` `<type>` | Source type recorded in frontmatter. Default: `file`. |
| `--title` `<title>` | Title, used for the filename and frontmatter. |

**`wiki archive`** — Retire an article to archive/, keeping it readable

| Flag | Description |
|------|-------------|
| `<slug>` | Article to retire. |
| `[<dir>]` | Where to start looking (default: cwd). |
| `--wiki` `<dir>` | Use this knowledge base instead of searching. |
| `--force` | Archive even while live articles link to it. |

### `workbench-reference`

Print a reference card of all workbench skills, agents, and reuse modes.

Called by the `reference` skill (`ai/skills/reference`). Discovers from SKILL.md
frontmatter under `~/.claude/skills/` and agent `.md` files under
`~/.claude/agents/` (`CLAUDE_DIR` overrides that root).

Usage: workbench-reference

Stdout is markdown: a Skills table (invocation, description), an Agents table
(name, description), and a Reuse Modes table. Skill descriptions are trimmed at
the TRIGGER/SKIP/Use-when clause so the card stays short.

`-h` / `--help` prints this text.

### `workbench-rules`

Manage this machine's own coding-rule layers — the local additions and
overrides that no harness ships. Installing the merged set is each harness's
own sync step (`step_claude_rules` symlinks it into `~/.claude/rules/`,
`step_pi_guidelines` concatenates it into `~/.pi/agent/AGENTS.md`), so a
machine running either harness alone gets the same rules.

```
workbench-rules <command> [<args>]
```

| Command | Description |
|---------|-------------|
| `sync` | Regenerate `workbench.md` in the generated layer |
| `add <domain> "rule"` | Append a rule to `~/.config/workbench/overrides/ai/guidelines/rules/<domain>.local.md` |
| `list` | List all local rule files with line counts |
| `status` | Show local rules not tracked in workbench |
| `open [domain]` | Open a local rule file in `$EDITOR` |
| `project add "rule"` | Append a convention to the current repo's `CLAUDE.md` |
| `project show` | Display the current repo's `CLAUDE.md` |
| `-V`, `--version` | Show version |
| `-h`, `--help` | Show help |

Domain aliases: `ts`/`js` → `typescript`, `py` → `python`, `sh`/`shell` → `bash`, `yml` → `yaml`.

## Installed Tools

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

## Adding a Tool

See [Registries](registries.md#adding-an-entry) for the full schema and step-by-step instructions. A script in a `bindir` registry needs a header before it can ship: the reference above is rendered from it, and the build fails when a script in the reference has none.
