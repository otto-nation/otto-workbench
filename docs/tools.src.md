---
title: Tools & Scripts
description: Complete catalog of workbench scripts, installed tools, and shell aliases, generated from the tool registries.
---

# Tools & Scripts Reference

Complete catalog of workbench scripts, installed tools, and shell aliases. Auto-generated from [tool registries](registries.md) — do not edit the generated sections directly.

## Scripts

<!-- include: bin/local/generate-tool-context --emit scripts-table -->

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

<!-- include: bin/local/generate-tool-context --emit workbench-commands -->

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
| `rebase [--fix] [--push] [--abort] [--onto <ref>]` | Rebase onto the branch's base — `--onto`, else the PR's base branch, else the repo's default branch |
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
([`ai/lib/tool_parser.py`](../ai/lib/tool_parser.py)) inherit the flag for free.

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
non-zero exit, malformed JSON, a schema missing `name` or `input_schema`, or a probe that
outruns the timeout — is logged at warning level on stderr with the reason. A tool you
added that never appears in an MCP client is explained there. Executables with no marker
are not tools and are skipped without comment.

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
the directories, the candidate filter, the probe itself, and the registry lookup — and
fails when a candidate in the checkout cannot answer or no registry entry names it, rather
than leaving the tool to vanish at runtime. Visibility is not checked: a `hidden` entry is
a decision somebody made, and the probe has to cover the script anyway because `pr` runs
it.

**Staying current without a restart.** The client owns this process — it spawns the server
over stdio and nothing outside can restart it — so a tool added, re-signatured, or
registered differently after startup would otherwise stay invisible until the next client
session. Every couple of seconds the server fingerprints what discovery reads: the scanned
directories, every file in them (modification time, size, and mode, since `chmod +x` is the
whole of what turns a file into a candidate), and every `registry.yml`. Nothing is executed
and no source is read, so a poll that finds nothing costs one `stat` per file; the interval
is a bound on staleness rather than a cost to trade against.

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
| `init` | Add Serena to `.mcp.json` in the current directory (creates if missing) |
| `status` | Show whether Serena is configured in the current project |
| `-h`, `--help` | Show help |

## Installed Tools
<!-- include: bin/local/generate-tool-context --emit tools-table -->

## Adding a Tool

See [Registries](registries.md#adding-an-entry) for the full schema and step-by-step instructions.
