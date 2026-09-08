---
title: Tools & Scripts
description: Complete catalog of workbench scripts, installed tools, and shell aliases, generated from the tool registries.
---
<!-- Generated from docs/tools.src.md by bin/local/compose-docs — do not edit. -->

# Tools & Scripts Reference

Complete catalog of workbench scripts, installed tools, and shell aliases. Auto-generated from [tool registries](registries.md) — do not edit the generated sections directly.

## Scripts

| Script | Description |
|--------|-------------|
| `pr` | Unified PR lifecycle CLI — CI failures, code review, and review comments |
| `claude-review` | Run Claude's reviewer agent on a PR with local worktree checkout and iterative review support |
| `otto-log` | Query the unified trail root and AI usage across otto-workbench scripts — audit trail plus cost and token stats |
| `workbench-rules` | Manages this machine's own coding-rule layers — local additions and overrides, for whichever harnesses are installed |
| `ceiling-scan` | Scan for ceiling: and ceiling-permanent: markers and produce a structured debt ledger |
| `dream-scan` | Scan session transcripts and memory state for dream consolidation |
| `dream-verify` | Verify dream memory file integrity across all projects |
| `promote-scan` | Scan memories and workbench artifacts for promotion evaluation |
| `retro-scan` | Scan PR review comments and cross-reference against coding rules |
| `workbench-reference` | Reference card — lists all workbench skills, agents, and reuse modes |
| `ci-check` | Fetch CI run data, classify failures, and output status dashboard |
| `pr-rebase` | Rebase current branch onto its base with conflict detection and force-push |
| `pr-describe` | Revise the PR description against the repo's PR template once the branch stops moving |
| `review-orchestrate` | Review orchestration engine for claude-review — manages tier classification, file grouping, and review merging |
| `review-post` | Deterministic posting of review findings to GitHub as a PENDING PR review |
| `review-rebuild` | Rebuild review.md from group finding files — recovers from synthesis formatting drift |
| `review-threads` | Thread lifecycle status for PR review comments — dashboard and JSON report |
| `review-thread-triage` | Deprecated — redirects to review-threads --triage |
| `validate-review-positions` | Validates review finding positions against a PR diff to ensure comment placement accuracy |
| `ai-usage-log` | Bridge shell-invoked AI calls into the global usage ledger — render, unwrap, record |
| `eval-models` | Evaluation runner — scores AI calls against a corpus, one task per manifest |
| `otto-mcp-server` | Dynamic MCP server — discovers tool-schema scripts and exposes them to MCP clients |
| `build-otto-ai-tools-tarball` | Package otto-ai-tools into a self-contained tarball for distribution |
| `build-claude-config-tarball` | Package Claude Code configuration into a tarball for server or container deployment |
| `workbench-export` | Export workbench Claude configs as a self-contained tarball, filtered by profile |
| `task` | AI-powered Git automation runner; wraps go-task with global/local Taskfile routing |
| `otto-workbench` | Manage your workbench developer environment |
| `mem-analyze` | macOS memory analysis report — pressure, swap usage, top processes, per-user totals |
| `wt-cleanup` | Remove stale git worktrees — merged branches and optionally age-based cleanup |
| `resolve-branch` | Resolve a fuzzy branch name to an exact git branch — tries exact, worktree, separator, fuzzy |
| `resolve-worktree` | Print the worktree a bare-repo container stands in for — the checkout of its default branch |
| `wt-init` | Convert a regular git repo to a bare repo with worktrees |
| `lint-sweep` | Sweep lint violations across multiple Go repos — detect, report, and optionally create fix branches |
| `validate-nesting` | Validate bash, Python, and Go script nesting depth to enforce flat control flow |
| `gcloud-reauth` | Check GCP application-default credentials and re-login if expired, with self-managed launchd agent |
| `get-secret` | Interactively retrieves a secret from AWS Secrets Manager by listing and selecting |
| `claude-bash-guard` | PreToolUse hook for the Bash tool — blocks command shapes that trigger unsuppressible permission prompts |
| `reuse-mode-tracker` | Track /reuse lite|full|ultra commands via UserPromptSubmit hook |
| `reuse-session-start` | SessionStart hook — inject reuse level and ceiling scan nudge |
| `reuse-subagent-start` | SubagentStart hook — inject reuse level into spawned subagents |
| `workbench-statusline` | Status line script — displays reuse level, ceiling debt count, and review status |
| `run-auto-task` | Run a Claude Code skill as a headless background session with output logging |
| `serena-mcp` | Scaffolds Serena MCP into a project's .mcp.json for project-scoped code intelligence |
| `run-tests` | Runs the bats and pytest suites with the repo's parallelism settings — the single entry point used by the Taskfile, the pre-push hook, and CI |
| `validate-all` | Runs every validator discovered in bin/ and bin/local/ — the single entry point used by the pre-push hook and CI |
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
| `validate-stat-portability` | Validates that stat format flags are confined to the lib/portable.sh helpers |
| `validate-script-loading` | Validates that only tests/conftest.py executes a module out of a file, so one script never has two module objects |
| `validate-permissions` | Validates that every Bash permission rule can match a command, that no untracked settings file duplicates a tracked grant or re-grants a gated one, and that a tracked allow bucket is in the codepoint order both ai sync and Claude Code write it back in — --fix prunes the duplicates and sorts the bucket |
| `validate-ceiling` | Validates that every ceiling marker names an upgrade trigger or is marked permanent |
| `validate-tool-schema` | Validates that every script claiming the --tool-schema protocol can answer the MCP server's probe |
| `validate-eval-baselines` | Validates eval baseline files for schema correctness and corpus coverage |
| `validate-docs-composed` | Validates that every composed doc matches what its docs/*.src.md composes to |
| `validate-doc-reference` | Validates that a source doc renders every module group its source set declares |
| `validate-doc-budget` | Validates that a doc declaring a line budget stays within it and holds no '####' heading |
| `validate-tracked-ignored` | Validates that no tracked file lives under a path .gitignore claims to ignore |
| `validate-rules` | Validates rule frontmatter conventions — harness scoping resolves, no Claude-only tool vocabulary in a rule that reaches Pi |
| `compose-docs` | Composes docs/*.md from docs/*.src.md by expanding include directives into generator output |
| `generate-doc-reference` | Renders a module reference from the doc blocks of a source set's own modules |
| `generate-tool-context` | Generates tools.generated*.md rule files from the domain registries |
| `generate-config-schema` | Generates config.schema.json and the docs key reference from WorkbenchConfig |
| `generate-public-surface` | Generates the per-package public surface snapshot from the registries, config schema, and shipped artifacts |
| `validate-public-surface` | Validates that the committed public surface snapshots match the registries, config schema, and shipped artifacts they are generated from |
| `check-surface-compat` | Fails when a public surface entry is removed without a breaking-change or Not-Breaking declaration |
| `cleanup-testcontainers` | Stops and removes stale Testcontainers Docker resources left by test runs |
| `generate-changelog` | Generates a changelog from conventional commits grouped by type |
| `wt-fetch-default` | Brings the local default branch up to date with origin's — the worktrunk pre-switch hook |
| `ghostty-terminfo-push` | Installs Ghostty's xterm-ghostty terminfo on a remote host — fixes 'Error opening terminal' over SSH |
| `aliases` | Lists all custom shell aliases and functions with optional keyword filtering |

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

A merged worktree holding uncommitted changes is reported rather than removed, and the
question of what counts as a change is asked of the default branch rather than of the
worktree's own index. The branch is already in the default branch, so the default
branch's ignore rules are the ones that decide whether a file is worth preserving —
and a worktree cut before a rule landed does not carry it, which is the only reason git
reports the file at all. A file whose lines merely moved is forgiven on the same
grounds: a reordering of a branch that already landed is residue, not work.

Nothing else is forgiven. A file the default branch does not ignore, one that gained or
lost a line, a staged change, a rename, and a deletion all still hold the worktree back
and are named in the summary. Every path the check forgives is written to
`~/.local/state/workbench/logs/wt-cleanup.log` with its reason, so a removal this
widens can be audited afterwards.

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

### `resolve-worktree`

Print the worktree a bare-repo container stands in for — the checkout of its default branch.

```
resolve-worktree [<path>]
```

| Flag | Description |
|------|-------------|
| `-h`, `--help` | Show help |

`<path>` defaults to the current directory. The default branch is the one the container's own `HEAD` names — git guarantees that ref exists, it needs no remote, and it is what `git clone` reads to pick the branch a new checkout lands on. `refs/remotes/origin/HEAD` is deliberately not consulted: `git clone --bare` creates no remote-tracking refs, so reading it would mean guessing between `master` and `main` for most containers.

| Exit | Meaning |
|------|---------|
| `0` | Resolved; the worktree path is on stdout |
| `1` | A bare repo, but no worktree holds the default branch (or its `HEAD` is detached) |
| `2` | Not a bare repository — nothing to resolve |
| `64` | Usage error |

Exit `2` is the ordinary answer for an everyday repo, a worktree, or a directory outside any repo, so callers treat it as "carry on here" rather than a failure. This is the one owner of that resolution in bash. The [`claude` shell wrapper](architecture.md#shell-zsh) calls it directly — redirecting a launch is all it wants. A tool that resolves a tree in order to *write* a project artifact into it calls [`lib/worktree.sh`](libraries.md)'s `project_root` instead, which lets a working tree name itself first and falls back to this only when there is none; the ceiling-debt Stop hook, `serena-mcp`, `workbench-rules`, and `otto-workbench ai init` all reach it that way.

[`lib/permission_mirror.py`](libraries.md) applies the same rule in Python to pick the worktree a container's [permission mirror](../CONTRIBUTING.md#permission-grants) is copied from. The two must agree — a session redirected to a worktree the mirror never wrote from is a session missing the grants the mirror exists to deliver, with nothing to say so — and `tests/container_source.bats` fails if they diverge.

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
| `--no-<phase>` | Skip an optional phase of a multi-phase review — see below | — |
| `--disprove` | Run the disprove gate even when the effort preset drops it | effort-based |
| `--json-summary` | Suppress human output; emit JSON summary to stdout | — |
| `--issue <link>` | Attach an issue link for reviewer context | — |
| `--max-parallel <N>` | Max concurrent group reviews | `4` |
| `--max-cost <USD>` | Max total review cost in USD | `20` |
| `--model <name>` | Override model for all agents (e.g., `sonnet`, `opus`) | — |
| `--repo-dir <path>` | Path to local repo or worktree (aliases: `--repo`, `--worktree`) | auto-detected |
| `-V`, `--version` | Show version | — |
| `-h`, `--help` | Show help | — |

`--no-post` and `--post` are mutually exclusive.

#### Switching a phase off

Every phase the multi-phase pipeline has a path around is marked `optional` in `PHASES` ([`ai/lib/agent/registry.py`](../ai/lib/agent/registry.py)), and each one gets a `--no-<phase>` flag generated from that mark: `--no-holistic`, `--no-scout`, `--no-group`, `--no-synthesis`, `--no-disprove`. Marking a new phase optional is the whole change — the flag, its forwarding to `review-orchestrate`, and the pipeline's skip all read the same field.

Phase 1 is one scan chosen from two candidates, so `--no-holistic` alone falls back to the scout scan and `--no-scout` alone falls back to the holistic scan. Only both together drop phase 1.

`--no-group` and `--no-synthesis` leave the review partial rather than clean: the merge still runs and reports every group as `skipped`, and the status header says `partial`. A review that reviewed nothing must not read like one that found nothing.

The `--effort` preset drops phases the same way — `low` skips the phase-1 scans, synthesis, and the disprove gate. `--disprove` buys the gate back from a preset that dropped it; `--no-disprove` beats `--disprove` and switches it off outright.

#### What self-review reads

`--self` reviews the worktree, not the remote branch. Everything that differs from the base branch is in scope: unpushed commits, staged and unstaged edits, and untracked files (`.gitignore` still applies). The head SHA and the changed-file list come from `git`, never from GitHub. When the branch already has a PR, its title, body and labels supply context but do not define the diff — the review logs the local head whenever it differs from the PR's.

Re-reviews narrow to what changed since the prior review, and that delta follows the same rule — uncommitted work done since the last `--self` run is picked up. PR mode is unaffected: it reviews the pushed commits only.

#### Model selection

Each pipeline phase resolves its model as **`--model` flag > `WORKBENCH_AI_<PHASE>_MODEL` > `WORKBENCH_AI_MODEL` > phase default**. The phase names and their defaults live in `PHASES` ([`ai/lib/agent/registry.py`](../ai/lib/agent/registry.py)) — the env key is derived from each name by convention, so adding a phase needs no change here.

Bare aliases (`sonnet`, `opus`, `haiku`) resolve through `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, and `ANTHROPIC_DEFAULT_HAIKU_MODEL` when those are set; otherwise the alias is passed to the CLI as-is.

#### Vertex AI quota preflight

When the Claude backend is pointed at Vertex AI, the review aborts before spending anything if a model it would use has no provisioned quota in the target project. The env vars are declared in [`ai/lib/vertex.env.yml`](../ai/lib/vertex.env.yml) and scaffolded into `~/.env.local`.

The gate is fail-open: it only stops runs it can prove are misconfigured. It proceeds — with a note — when the CLI is not on Vertex, when project/region are unset, when there are no application-default credentials, when the Service Usage API errors, or when the model is a bare alias the CLI resolves internally. On failure it lists the provisioned models and names the `WORKBENCH_AI_<PHASE>_MODEL` keys worth changing. Quota lookups are cached per project/region for 5 minutes in `${WORKBENCH_CACHE_DIR}/vertex-quota/` — see [Libraries — roots.sh](libraries.md#rootssh).

Requires application-default credentials (`gcloud auth application-default login`) with read access to `serviceusage.googleapis.com`. The check is skipped entirely on non-Claude backends (`AI_BACKEND=pi`).

### `workbench-rules`

Manages this machine's own coding-rule layers — the local additions and overrides that no harness ships. Installing the merged set is each harness's own sync step (`step_claude_rules` symlinks it into `~/.claude/rules/`, `step_pi_guidelines` concatenates it into `~/.pi/agent/AGENTS.md`), so a machine running either harness alone gets the same rules.

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
| `ci [--fix] [--post]` | Fetch and classify CI failures; `--fix` attempts automated repair, `--post` pushes the fix |
| `review [--self] [--fix] [--post] [--repair] [--summary]` | Run code review via `claude-review` |
| `comments [--triage] [--fix] [--finish] [--track THREAD_ID] [--track-all] [--post] [--reply <id> --body-file <path> --post] [--settle <id> --as <outcome>]` | Fetch and manage PR review threads (see phases below); `--post` publishes (default: drafts) |
| `fix` | Run fix passes for CI, review, and comments in one step, then revise the description |
| `rebase [--fix] [--push] [--abort] [--onto <ref>]` | Rebase onto the branch's base — `--onto`, else the PR's base branch, else the repo's default branch |
| `describe [--force] [--dry-run]` | Revise the PR description against the repo's PR template |
| `gc` | Clean up stale PR review artifacts and cached state |

**Every AI call `pr` makes is a phase.** Not only the review pipeline's: the
conflict resolutions and lockfile commands behind `pr rebase --fix`, the
description `pr describe` writes, and the thread triage `pr comments` runs each
resolve their model and thinking level from `PHASES`
([`ai/lib/agent/registry.py`](../ai/lib/agent/registry.py)) through the same
chain, so `WORKBENCH_AI_REBASE_MODEL`, `WORKBENCH_AI_DESCRIBE_THINKING` and
their siblings move calls that used to take whatever the CLI defaulted to. The
env keys are derived from the phase name, so the list here is the registry's.

**`pr comments` flags fall on two axes — phase and gate:**

The phase flags (`--triage`, `--fix`, `--finish`, `--reply`, `--settle`) choose
which work the run does. `--post` is the gate: it decides whether that work
leaves the machine. Every phase drafts to stderr and publishes nothing without
it, so `--post` neither implies a phase nor is implied by one.
`--finish --post` is therefore not saying the same thing twice — the first
names the work, the second opens the gate. The same `--post` gates `pr review`
and `--reply`; it is one switch for the whole process, not a `comments` flag —
see `ai/lib/core/publishing.py`, which owns it.

**Every fix pass answers to the same gate.** `pr ci --fix` and `pr review --fix`
commit what their agent fixed and draft the push without `--post`, exactly as
`pr comments --fix` does. The commit always happens: it is local, it is what
makes the work reviewable, and it keeps the next round from reading its own
dirty tree as a refused commit. The push is the outward act, so it waits. A held
push prints the command that would send it, and every outcome short of a landed
push carries that command as data — `ai/lib/git/land.py` owns the commit and the
push under it, and `push.resume_command` renders the one thing to run.

Alongside `--fix`, `--post` is a modifier rather than a mode: `pr review --fix
--post` means publish what this run produces — post the findings, push the
commit — while `pr review --post` on its own publishes the review already on
disk. One caveat on `pr ci --fix --post`: the rebase it may run first pushes
through a subprocess the gate does not reach, so that push happens either way.

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

**`--settle` records a thread you handled yourself:**

`--fix` marks a thread `needs_human` when it is contested, ambiguous, or too
complex to attempt, and marks it `deferred` when it ran out of budget. Either
way the ending is usually the same: you write the fix, commit it, and push. None
of that reaches the snapshot, so `--finish` goes on reporting a settled thread as
open — no reply, no resolution, an Action cell reading `Needs discussion`, and a
closeout `pr status` keeps quoting as owed.

`--settle <id>` is how you tell the CLI what you did. It is repeatable, and
`--as` picks which of the three terminal outcomes to record — `fixed` (the
default), `dismissed`, or `already_addressed`. From there the thread is
indistinguishable from one the pass settled: `--finish` replies on it, resolves
it, and gives it a summary row attributed to the commit that carries the change.

- `--as dismissed` requires `--reason <text>`, which becomes the body of the
  reply. Telling a reviewer their point does not apply, with nothing for them to
  argue with, is worse than not replying. The other two outcomes render no
  reason and refuse the flag rather than swallowing it.
- `--as fixed` finds the commit itself, from the branch history of the line the
  thread is anchored to, and cites it only once the remote has it — a link to a
  commit still sitting on your machine 404s for the reviewer it was written for.
  Pass `--commit <sha>` when the fix landed somewhere else; a `--commit` the
  remote does not hold is an error rather than a dropped citation. When neither
  resolves, the row reads `Addressed outside the fix pass` and the run says so.
  `--finish` renders that same cell for a thread it reconciled against GitHub.
  Neither is credited afterwards to whatever commit last touched the line the
  thread is anchored to: that history is evidence about a fix the pass landed,
  and both of these say it did not. What withholds the citation is the record of
  who settled the row, not the wording of the reason printed beside it.
- Naming an id no fix pass recorded is an error listing the threads that are
  waiting on a person, for the same reason `--track` errors on one. Recording
  the outcome a thread already carries is a no-op that says so; recording a
  different terminal outcome replaces it and reports what it replaced.

Recording is its own step, so `--settle` publishes nothing and refuses `--post`
and every other phase flag alongside it. Run it, read the state back, then run
`pr comments --finish --post` — the same gate every other write here answers to.

That per-line search is also what attributes a pass whose own commit never
landed — a hook rejected it, or you committed the work yourself before
publishing. The branch having moved says work landed outside the pass; it does
not say which row any of it carries, and the number of commits that landed
cannot make it say so. A fix record spans rounds, so it holds rows an earlier
round settled and recorded no SHA for, which the newest commit provably does not
contain. So the moved HEAD supplies a tree for the file permalinks and nothing
more, and every uncredited row is resolved from its own line history. A row that
cannot be — its line's only commit predates the review, or it was decomposed out
of a review-level comment and is anchored to no line — reads
`Fix applied (commit not recorded)` rather than borrowing a commit.

**Replies are one per thread:**

Every reply — generated by `--fix`/`--finish` or hand-written via `--reply` —
edits our standing reply in place while that reply is still the last comment on
the thread, and posts a new one only once a reviewer has answered. Editing under
a reviewer's reply would rewrite the text they were responding to; leaving a
second comment when nobody has answered leaves them holding two of our positions
with no way to tell which stands. Whether the thread is resolved makes no
difference: `--finish --post` resolves the threads it answers, and the reply it
left there is still the one to revise.

A reply that no longer reads as one of ours was rewritten by hand, and the
thread is then left alone for the life of the PR — the round logs it and moves
on rather than posting a templated answer under a position a person already
stated. A reviewer answering does not retire that: the thread having become a
conversation is the strongest reason not to talk over it. Use
`--reply <id> --body-file <path> --post` to replace a hand-written reply on
purpose.

`--reply <id> --body-file <path> --post` accepts a thread node ID, any comment
`databaseId` in the thread, or a `...#discussion_r<id>` URL, and warns when the
body carries no `blob/<sha>/` permalink to back its claims. Pass `-` as the path
to read the body from stdin. Like every other write here it needs `--post`;
without it the body is printed under `DRAFT (not published)` and nothing is
sent. Use it instead of `gh api .../replies`, which bypasses the dedup entirely.

**The summary is a chain of comments, one per round:**

A review cycle posts `Review Comments Addressed` comments as it goes. A round
nobody has spoken over since the last one edits that comment in place; a round a
reviewer has commented, reviewed, or replied below posts a new one, because an
edit notifies nobody. Any of the five thread outcomes can appear — fixed,
already addressed, dismissed, deferred, and the ones still awaiting discussion.

A comment covers its own round rather than the whole PR: a thread it has not
seen before, one a reviewer has spoken on since the last summary, and one this
round gave a different outcome than the comment chain already reports. A thread
quiet since the round that published it is left in that comment and counted in a
note — one note for the settled ones, a second for the ones still awaiting an
answer, because "settled" is the wrong word for a question still owed one. A
footer links every earlier summary, so the newest comment is the entry point to
the whole record and an open thread is one link away rather than restated for
the life of the PR.

Re-classification is read as an outcome, not as cell text. One outcome has
several wordings — a fix reported with a commit and the same fix reported
without one — so a round that only re-words a cell changes nothing and the row
stays where it was published. A cell somebody rewrote by hand states no outcome
at all: it is held as published, never treated as a change, and never restated.

An edit is the case that can destroy a row, since it replaces a body. The
replacement is built from local state, which is per-target and per-worktree, and
routinely absent for a round the comment already covers: `pr gc`, a recreated
worktree, a later round run from another machine. So the comment is read before
the edit and any row this run cannot account for is carried forward verbatim,
counted as `N carried over`, and logged. An edit never drops a row it is the
only comment holding; a row an earlier comment also carries is scoped like any
other, since the chain still holds it.

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
not be filed — no tracker configured, a provider that cannot create issues, a
tracker keyed by team with no team to name, or a creation that failed — is
recorded the same way. `pr status` reads those flags back out, so the debt is
visible after the stderr line has scrolled past:

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
([`ai/lib/core/tool_parser.py`](../ai/lib/core/tool_parser.py)) inherit the flag for free.

**Where it looks.** The workbench's own script directories, and nothing else. They are
derived from the component layout rather than listed — the root `bin/`, plus every
`<component>/bin` and `<component>/<sub>/bin` in the checkout. That is the same two-level
glob [`lib/components.sh`](../lib/components.sh) uses for `steps.sh` and `migrations`, so
a new component tier such as `editors/zed/bin/` is scanned the moment it exists. Note it
scans the checkout, not the `~/.local/bin` those scripts are symlinked into: discovery
probes a candidate by running it, and `~/.local/bin` also holds everything else you have
installed.

**There is no configuration file.** The server hosts the workbench's own tools, so what
to scan is a fact about the checkout — there is nothing to hand-author and nothing to keep
in sync. An earlier design read `tool_dirs` and `plugin_dirs` from
`~/.config/workbench/mcp-tools.json` to let outside directories register tools; no setup
step ever wrote that file, no machine was found holding one, and the keys were removed
rather than carried into `config.yml`. Adding a tool means putting a `--tool-schema`
script in a component's `bin/` and registering it, as below.

Discovery reads each candidate's source before running it, and only executes the ones
carrying a protocol marker — a script that ignores unknown flags would otherwise do its
real work when probed. Scripts that mention the flag in prose without implementing it
must word around the literal to stay out of the probe path.

Carrying a marker is a claim to be a tool, so a candidate that then fails to answer — a
non-zero exit, malformed JSON, or a schema missing `name` or `input_schema` — is logged at
warning level on stderr with the reason. A tool you added that never appears in an MCP
client is explained there. Executables with no marker are not tools and are skipped
without comment.

**How a script is run.** Both the probe and a tool call go through one spawn helper, which
gives the child two things `subprocess.run` does not. Its stdin is closed rather than
inherited: the server's own stdin *is* the stdio JSON-RPC stream the client writes requests
into, so a script that reads a single byte takes that byte out of the transport and the
session dies on a parse error naming no tool. The probe is the likeliest reader — a script
that does not recognise `--tool-schema` falls through to its real work, and that work may
read stdin.

The child also gets a session of its own, so an expired bound `SIGKILL`s the whole process
group instead of the one process. A tool spawns agents — `pr review` is the case — and
signalling only the direct child leaves them running against the account with nothing
holding a handle to them. There is no grace window before the kill: the budget has already
expired, and a TERM-then-KILL ladder would double the worst case on a call that is already
late.

**A probe that never answers is a different finding.** The probe prints a schema the
script already holds, so it belongs in the `QUICK` tier of
[`ai/lib/core/timeouts.py`](../ai/lib/core/timeouts.py) and a breach is a wedged process or a machine
with nothing left to schedule — not a broken tool. It is logged at error level and worded
that way, because the two want different people to look at them. The bound used to be a
2-second local constant, which is under the cost of starting a Python interpreter on a
loaded machine; a probe that outran it dropped the tool for the whole session, since
re-discovery only runs when the scanned directories change.

Two things pay for the more generous bound. Candidates are probed concurrently, so what a
client waits for at startup is one probe rather than one per tool, and a probe that ran out
of time is tried once more — only the ones that timed out, and all of them together, so the
retry costs one more bound for the round rather than one per tool. Results are reported in
path order whatever order they finish in, so two scans of the same tree agree.

**What a client is offered.** Carrying the marker makes a script probeable, not public.
The registries decide who sees it: an entry with `visibility: full` or `brief` is offered,
one with `visibility: hidden` is not, and a script no registry entry names is not either.
The filter runs before the probe, so a script a client will never see is also never run at
startup. Today only `pr` is offered — `ci-check`, `pr-describe`, and `pr-rebase` are
registered hidden because they are what `pr ci`, `pr describe`, and `pr rebase` run, and
offering them beside `pr` asks a client to choose between a tool and its own internals.

The description a client reads is the registry's, not the script's: the registries own tool
documentation, and a `full` entry's `when_to_use` and `usage` lines are appended to it —
they answer a caller's real questions, and a client has no access to the rule files they
otherwise render into. A script's own `--tool-schema` description is written for its
`--help` and has already drifted shorter.

Those warnings belong to whichever MCP client spawned the server, so the same claims are
checked at build time by `bin/local/validate-tool-schema`. It imports this discovery —
the directories, the candidate filter, the probing round itself, and the registry lookup —
and fails when a candidate in the checkout cannot answer or no registry entry names it,
rather than leaving the tool to vanish at runtime. Visibility is not checked: a `hidden`
entry is a decision somebody made, and the probe has to cover the script anyway because
`pr` runs it.

**Two scripts, one name.** Discovery keys on the name a script answers with, not on its
filename, so two scripts can claim one tool. At runtime the first the scan reached wins and
the other is logged at error level naming both paths. Raising instead would run in the
re-discovery thread as well as at startup, where one ambiguity would either take the server
down or stop re-discovery for the session — first-wins leaves a working tool working. Which
of the two a client actually reaches is then decided by directory order, so
`bin/local/validate-tool-schema` fails the build on a collision. That is the only place it
can be an error rather than a log line.

It carries the same split. A probe that ran out of time is counted and reported apart from
the broken ones and points at the runner's load rather than at the script — on an
oversubscribed build runner that is what actually happened, and reporting it as a tool that
"cannot answer `--tool-schema`" sent readers after a script that was fine. It still fails
the run: a tool nobody could verify is a tool that may not reach a client.

**Staying current without a restart.** The client owns this process — it spawns the server
over stdio and nothing outside can restart it — so a tool added, re-signatured, or
registered differently after startup would otherwise stay invisible until the next client
session. Every couple of seconds the server fingerprints what discovery reads: the scanned
directories, every file in them (modification time, size, and mode, since `chmod +x` is the
whole of what turns a file into a candidate), and every `registry.yml`. Nothing is executed
and no source is read, so a poll that finds nothing costs one `stat` per file; the interval
is a bound on staleness rather than a cost to trade against.

The baseline every poll compares against is stamped before the startup scan, not after it,
and travels with the tool set that scan produced. A baseline taken later would already hold
whatever landed in between, so no poll would ever see that file appear and the tool would
stay missing for the whole session. Stamping first costs at most one redundant re-scan on
the first poll.

When the fingerprint moves, discovery runs again. Only a change to the offered set is
announced — pulling a branch touches many files and usually changes no tools — and the
announcement is `notifications/tools/list_changed`, which the server advertises as the
`tools.listChanged` capability during initialization. A client that never saw that promise
has no reason to re-list, so the notification and the capability ship together. Nothing is
sent before the client's first request, since a notification arriving mid-handshake is one
a client is entitled to reject.

A tool that was working and now is not is logged at error level with the reason it stopped
answering — its script is gone, it exited non-zero, or its registry entry no longer offers
it. A silent disappearance from `tools/list` is the failure this exists to prevent: the
client shows one fewer tool and says nothing about why.

**What a call returns.** Stdout that parses as JSON comes back as the text content of the
result, so a client sees the tool's own output rather than a rendering of it. A tool whose
schema declares `output_schema` returns that JSON as structured content as well, because a
client validates the reply against the schema `tools/list` advertised and rejects a text-only
answer before the caller sees it. Such a tool that prints no JSON object is therefore a
contract breach, not a plain result: the call comes back as an error naming the tool and
quoting the head of what it did print. Tools with no `output_schema` return text and
nothing more.

That contract is per tool, and `pr` is one command wrapping nine subcommands, so it
declares none. It used to declare `PRState` for every invocation, which only `pr status`
prints — and not even that one before a state file exists — so the other eight came back
`isError` for printing no JSON object. Declaring an output schema is worth it once it can
be per subcommand.

The launcher runs `uv run --no-project --with mcp`. A client spawns the server with its own
project as the working directory, and without `--no-project` uv would resolve and install
that project first — writing a virtualenv and a lock file into somebody else's checkout,
and failing outright where the project does not build. The server needs `mcp` and nothing
from wherever it was launched.

### `serena-mcp`

Scaffolds Serena MCP into a project's `.mcp.json` for project-scoped code intelligence.

```
serena-mcp <command>
```

| Command | Description |
|---------|-------------|
| `init` | Add Serena to `.mcp.json` in the current project (creates if missing) |
| `status` | Show whether Serena is configured in the current project |
| `-h`, `--help` | Show help |

`.mcp.json` and `.gitignore` are both tracked project files, so the project is the
working tree the current directory belongs to rather than the directory itself. A
shell sitting in a bare-repo container has no working tree, and
[`resolve-worktree`](#resolve-worktree) names the one the container stands in for;
a container whose default branch has no checkout is an error rather than a write
into the container, where nothing would read the file and no `.gitignore` rule,
review, or CI check could reach it. A directory outside any repository is left
alone — scaffolding there is a real thing to want.

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

See [Registries](registries.md#adding-an-entry) for the full schema and step-by-step instructions.
