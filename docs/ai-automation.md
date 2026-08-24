---
title: AI Automation
description: Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.
---
<!-- Generated from docs/ai-automation.src.md by bin/local/compose-docs — do not edit. -->

<!-- doc-budget: 400 -->

# AI Automation

Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.

This page is the operator's view: what to install, what to run, and what the
commands do. Behaviour that belongs to one module — how findings are numbered,
what the publishing gate covers, how a run's bookkeeping is keyed, what a
supersession preflight reads — is documented on that module in
[AI Libraries](ai-libraries.md), so there is one copy of it and it sits next to
the code it describes.

## Setup

First-time setup:

```bash
ai/setup.sh
```

Prompts for confirmation at each step. Safe to re-run. This installs Claude Code configuration, rules, skills, and agents.

After setup, configure the AI tool for global task automation:

```bash
task --global ai:setup
```

This creates `~/.config/task/taskfile.env` with:
- `AI_COMMAND` — which AI tool to use (e.g., `claude -p --output-format json --agent ci-cd`)
- `GH_TOKEN` — GitHub PAT for PR automation (fine-grained, scoped to specific repos)
- `ANTHROPIC_API_KEY` — optional, for isolating automation API usage

## What Gets Installed

**Claude Code:**
- `~/.claude/settings.json` — permissions and deny rules (merged, not overwritten)
- `~/.claude/CLAUDE.md` — coding guidelines
- `~/.claude/rules/` — language and tool-specific rules (symlinked)

**Skills:** analyze-project, anatomy, architecture, ceiling-debt, ci-failures, dream, machine, pr-comments, pr-rebase, promote, reference, retro, self-review-fix — see [Skill Reference](#skill-reference) for invocation, output, and lifecycle details.

**Agents:**

| Agent | Description |
|-------|-------------|
| changelog | Generate categorized release notes and changelogs from git history. Used by task automation. |
| ci-cd | Generate commit messages and pull request descriptions from git context. Used by task automation. |
| debugger | Systematic code-level bug diagnosis. Read-only — traces through source code to find root causes. Never modifies anything. |
| explain | Fast text-in/text-out explainer. Answers questions from provided input without exploring files or suggesting edits. |
| incident | Structured production incident investigation. Read-only triage — gathers symptoms, checks recent changes, forms ranked hypotheses. Never modifies anything. |
| migrate | Analyze codebases for migration tasks and produce phased upgrade plans. Read-only — plans changes but does not apply them. |
| reviewer-lite | Lightweight code reviewer for group and angles phases. Receives pre-collected data — no context gathering needed. Produces categorized findings (must-fix, should-fix, nit). Never modifies anything. |
| reviewer | Structured code review for PRs and diffs. Read-only — produces categorized findings (must-fix, should-fix, nit). Never modifies anything. |

**MCP Servers:** otto-workbench

## Skill Reference

### `/analyze-project`

Analyze a project's codebase and populate scaffolded .claude/CLAUDE.md and .claude/rules/ files with project-specific conventions. TRIGGER when: user runs otto-workbench ai init, re-scaffolds with --force, or has empty .claude/CLAUDE.md or .claude/rules/ sections.

```
/analyze-project
```

**Output:** `.claude/CLAUDE.md, .claude/rules/`
**Trigger:** Run after otto-workbench ai init scaffolds a project, after --force re-scaffolds, or when .claude/CLAUDE.md or .claude/rules/ files have empty sections.

### `/anatomy`

Generate or refresh a project file index (.claude/anatomy.md) with per-file descriptions and token estimates. Helps Claude decide what to read before exploring. TRIGGER when: user wants an overview of codebase structure, before exploring an unfamiliar project, or after significant file changes. SKIP: user asks about a specific known file — read it directly.

```
/anatomy
```

**Output:** `.claude/anatomy.md`
**Auto-trigger:** on HEAD change (via Stop hook)
**Trigger:** Run to refresh the project file index before exploring an unfamiliar codebase, or after significant file changes.
**Skip:** Do not use when the user asks about a specific file they already know — just read it directly.

### `/architecture`

On-demand architecture.md refresh. Reads recent sessions and memory to identify architectural facts that are missing or stale, then proposes specific additions to .claude/architecture.md. TRIGGER when: user discovers wrong-software assumptions, adds a new service or role, or architecture.md is stale (last-reviewed >14 days). SKIP: memory consolidation (use dream); machine-level facts (use machine).

```
/architecture
```

**Output:** `.claude/architecture.md`
**Trigger:** Run after discovering wrong-software assumptions, adding a new service or role to a project, when architecture.md last-reviewed date is more than 14 days old, or after discovering container tool constraints.
**Skip:** Do not use for memory consolidation (use dream instead) or machine-level facts (use machine instead).

### `/ceiling-debt`

Scan for ceiling: markers and present the debt ledger. TRIGGER when: user asks about ceilings, deferred simplifications, or technical debt markers. SKIP: general tech debt discussion without ceiling markers.

```
/ceiling-debt
```

**Output:** `ceiling debt ledger to stdout (manual); .claude/ceiling-debt.md (auto)`
**Auto-trigger:** on-stop (via Stop hook)
**Trigger:** ceiling debt, show ceilings, what did we defer, list simplifications, ceiling markers
**Skip:** General tech debt discussion, architecture review, non-code requests

### `/ci-failures [<pr_number_or_run_id_or_branch>]`

Diagnose and fix GitHub Actions CI failures with run-aware progression tracking: fetch, classify, diagnose, fix, push, and monitor across workflow runs. TRIGGER when: user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red. SKIP: reviewing code (use code-review or pr review instead); addressing PR review comments (use pr-comments instead).

```
/ci-failures [<pr_number_or_run_id_or_branch>]
```
**Output schema:** `ci-check --tool-schema` (MCP tool: `ci-check`)
**Trigger:** Use when user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red.
**Skip:** Do not use for code review (use code-review or pr review instead); do not use for addressing PR review comments (use pr-comments instead).

### `/dream`

Memory consolidation for Claude Code. Scans session transcripts for corrections, decisions, preferences, and patterns, then merges findings into persistent memory files. TRIGGER when: user asks to consolidate memory, clean up notes, or after sessions with corrections and decisions. SKIP: project architecture facts (use architecture); machine profile updates (use machine).

```
/dream
```

**Output:** `memory/ topic files`
**Auto-trigger:** 24h (via Stop hook)
**Trigger:** Run to consolidate scattered memory notes, after multiple sessions with corrections or decisions, or when MEMORY.md is cluttered. Auto-triggers every 24h.
**Skip:** Do not use for project architecture facts (use architecture instead) or machine profile updates (use machine instead).

### `/machine`

Refresh the machine profile (~/.claude/machine/machine.md) — hardware, OS, runtimes, Docker, Git identity, and project registry. TRIGGER when: user upgrades tools, installs new runtimes, or machine.md is stale (>7 days). SKIP: project-specific architecture (use architecture); memory consolidation (use dream).

```
/machine
```

**Output:** `~/.claude/machine/machine.md`
**Auto-trigger:** 24h (via Stop hook)
**Trigger:** Run after upgrading runtimes, installing new tools, or when machine.md last-updated is more than 7 days old. Auto-triggers every 24h.
**Skip:** Do not use for project-specific architecture (use architecture instead) or memory consolidation (use dream instead).

### `/pr-comments [<pr_number_or_branch>]`

Analyze and address PR review comments with lifecycle tracking: fetch, classify, verify, fix, then draft replies for approval before publishing with --post. TRIGGER when: user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments. SKIP: initial code review requests (use code-review or pr review instead); self-review before PR creation (use self-review-fix instead).

```
/pr-comments [<pr_number_or_branch>]
```
**Trigger:** Use when user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments.
**Skip:** Do not use for initial code review requests (use code-review or pr review instead); do not use for self-review before PR creation (use self-review-fix instead).

### `/pr-rebase [branch] [--no-fix] [--no-push] [--force] [--onto|--base <ref>]`

AI-assisted rebase onto the branch's base with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against its base, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead).

```
/pr-rebase [branch] [--no-fix] [--no-push] [--force] [--onto|--base <ref>]
```
**Output schema:** `pr-rebase --tool-schema` (MCP tool: `pr-rebase`)
**Trigger:** Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against its base, or fix merge conflicts during rebase.
**Skip:** Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording (use task commit:reword instead).

### `/promote`

Reviews accumulated Claude Code memories for promotion into durable workbench artifacts — lint rules, scripts, coding rules, hooks. Prioritizes mechanical enforcement over prose. TRIGGER when: user wants to review memories for promotion, or after dream has consolidated corrections. SKIP: direct rule/script edits — just edit them; memory consolidation (use dream).

```
/promote
```

**Output:** `ai/memory/PROMOTE.md`
**Auto-trigger:** 7 days (via Stop hook)
**Trigger:** Run to evaluate accumulated memories for promotion into workbench artifacts, or after dream has consolidated several sessions of corrections and decisions. Auto-triggers every 7 days.
**Skip:** Do not use when the user wants to directly edit a rule or script — just edit it. Do not use for memory consolidation (use dream instead).

### `/reference`

Show a reference card of all workbench skills, agents, and reuse modes. TRIGGER when: user asks what skills/commands/agents are available, wants a quick reference, or asks how to use the workbench. SKIP: detailed help on a specific skill (invoke that skill directly).

```
/reference
```

**Output:** `formatted reference card to stdout`
**Trigger:** what skills are available, show commands, help, reference card, what can you do
**Skip:** Detailed help on a specific skill — invoke that skill directly

### `/retro`

Analyze PR review comments to identify gaps in coding rules. Fetches comments from all registered repos, classifies them against existing rules, and proposes specific rule additions or refinements. TRIGGER when: user wants to analyze review patterns for rule gaps, after a batch of PR reviews. SKIP: addressing comments on a specific PR (use pr-comments); memory consolidation (use dream).

```
/retro
```

**Output:** `ai/memory/RETRO.md`
**Auto-trigger:** 72h (via Stop hook)
**Trigger:** Run to analyze recent PR review comments for coding rule gaps, after a round of PR reviews has been completed, or when rule coverage feels incomplete. Auto-triggers every 72h.
**Skip:** Do not use when the user wants to address comments on a specific PR (use pr-comments instead). Do not use for memory consolidation (use dream instead).

### `/self-review-fix [branch_name]`

Run self-review and auto-fix findings. Wraps pr review --self --fix. Can also fix from an existing review without re-running. TRIGGER when: user asks to self-review a branch, run pre-merge review, or auto-fix findings before PR creation. SKIP: reviewing someone else's PR (use code-review or review); addressing existing PR review comments (use pr-comments).

```
/self-review-fix [branch_name]
```
**Trigger:** Use when the user asks to self-review a branch, run a pre-merge review, or auto-fix review findings before creating a PR.
**Skip:** Do not use for reviewing someone else's PR (use code-review or review instead). Do not use for addressing existing PR review comments (use pr-comments instead).

## Session Lifecycle

Skills with a cadence (shown in the table above) auto-trigger via Stop hooks in `settings.json`:

1. **Session exit** — Stop hooks run `should-<skill>.sh` cooldown checks. If due, creates `~/.claude/.<skill>-pending`
2. **Next session start** — Claude reads CLAUDE.md, sees the pending flag, runs the skill as a background subagent, then deletes the flag
3. **Skill completion** — completion script records a timestamp so the cooldown resets

Additionally, `wt-cleanup --quiet` runs on every session exit to remove stale git worktrees.

### Manual triggers

All lifecycle skills can be run on demand: `/ceiling-debt`, `/dream`, `/promote`, `/retro`, `/anatomy`, `/machine`.

## Task Automation

Use `--global` to run tasks from `~/.config/task/` rather than a local project Taskfile.

```bash
task --global ai:setup             # Setup AI configuration
task --global commit               # Generate AI-powered commit message based on staged changes
task --global commit:reword        # Reword a commit message with AI (default: HEAD; or: task reword -- SHA)
task --global pr:content           # Preview AI-generated PR title and description (-- --no-issue to skip issue prompts, -- --base <branch> to target a non-default base)
task --global pr:create            # Create AI-powered pull request (-- --no-issue, --draft, --base <branch>, --title <title>, --body <body>, --body-file <path>)
task --global pr:update            # Update current PR description (-- --no-issue, --base <branch>, --title <title>, --body <body>)
task --global review               # AI review of staged, unstaged, and committed branch changes
task --global pr:review            # AI review of the current PR
```

## Configuration

Override which AI tool the global Taskfile uses:

```bash
# ~/.config/task/taskfile.env
AI_COMMAND=claude -p --output-format json --agent ci-cd
```

Override per-project with `.taskfile/taskfile.env` in a project root.

### Usage ledger

Every AI call made through the workbench records what it cost. Query the ledger
with `otto-log stats`:

```bash
otto-log stats                      # last 7 days, grouped by script
otto-log stats --since 24h          # any h/d/m window
otto-log stats --by task            # or: script, model, day
otto-log stats --by day --json      # one JSON object per row
```

What is recorded, and what each column means, is documented on
[`ai_usage.py`](ai-libraries.md#ai_usagepy).

### Evaluating AI quality

The ledger says what a call cost; the eval harness says what it bought. `eval-models`
runs each case in [`eval/corpus/`](../eval/corpus/) through a throwaway git repo and
scores the result against that case's `manifest.json`.

```bash
eval-models --dry-run                          # validate the corpus, run nothing
eval-models --models sonnet,opus --runs 3      # compare models
eval-models --compare                          # diff against eval/results/ baselines
eval-models --save-baselines                   # record new baselines
```

A manifest's `task` field picks how its case is run and scored, and each task
pairs a runner with a scorer in `ai/lib/eval_scoring_<task>.py`. What a case of
each task holds is documented on [`eval_task.py`](ai-libraries.md#eval_taskpy);
how each is scored, on the task's own module —
[`review`](ai-libraries.md#eval_scoring_reviewpy),
[`ci-fix`](ai-libraries.md#eval_scoring_cifixpy),
[`skill`](ai-libraries.md#eval_scoring_skillpy). The runner, the fixture repo,
and the statistics over repeated runs are shared and know nothing about any one
task.

Landing a case costs a full eval run. `bin/local/validate-eval-baselines` fails
any corpus entry that no baseline in `eval/results/` covers, and
`bin/local/validate-all` discovers it by glob — so a new case is red on pre-push
and in CI from the moment it lands until a baseline records it. That baseline has
to come from a run over the whole corpus: `--save-baselines` rebuilds each
model's file wholesale from the entries of the run it is handed, so
`--entry <new-case> --save-baselines` writes a file holding that one entry and
drops every other. There is no filtered top-up.

`--compare` diffs a run against those baselines and exits `2` on a regression.
Which metrics that gate covers, and which are reported but never gated, is
documented on [`eval_scoring.py`](ai-libraries.md#eval_scoringpy).

The [`Eval` workflow](../.github/workflows/eval.yml) runs this weekly and on
demand. It is not a pull-request check: each run spends real money on real model
calls. Without `ANTHROPIC_API_KEY` configured it validates the corpus and stops.

### How a review's verdict is decided

`ReviewVerdict` owns the four verdicts in both spellings they are written in: the
word the prompt asks for and the review states (`Request changes`), and the value
recorded in state and shown by `pr status` (`changes_requested`). One member owns
both, so the prompt cannot ask for wording the parser does not recognise, and the
markdown cannot say one thing while the dashboard reports another.

The verdict a review is recorded with reconciles two readings of it — the prose
the agent wrote and the findings that survived verification — and the stronger
call wins:

- Findings that block cannot be under-reported. A review stating `Approve` with a
  must-fix finding still records `changes_requested`.
- A stronger call the agent made is not discarded. A review stating
  `Request changes` over nits alone keeps it.
- `Disapprove` is unranked and always stands: it judges the overall approach,
  which no finding count implies or refutes.
- A self-review records no verdict — it is advisory and has no PR to approve or
  block. `Disapprove` is the exception, since it holds without a PR.

Counts alone map to `Request changes` (any must-fix), `Needs discussion` (any
should-fix), or `Approve`. Nits and idioms do not affect the verdict, and a review
file that does not exist records no verdict rather than an approval.

### Which files the rebase fix pass is allowed to touch

When a rebase's force-push is rejected by a pre-push check, `pr rebase` runs a
fix pass over the conflicted files, using the project's own check output as the
oracle. The candidate list is every file the rebase resolved a conflict in —
which on a long branch is dozens of files, listed once per conflict rather than
once per file, while the check that failed names two of them.

The pass runs only on the candidates the check output names, matching either the
path or the bare filename so a check that reports a basename still scopes. Each
fix is a whole-file prompt, so the unscoped version spent a call per resolved
file; worse, the backend runs with `acceptEdits` and `Bash(*)`, so handing it a
file the check had no complaint about is an invitation to rewrite a file the
branch never touched. When the output names no candidate at all — a check can
fail without printing a path — every resolved file is still a suspect and the
pass falls back to all of them, recording that it did so, because that is the
expensive path and it should not be invisible in the trail.

A fix that fails records its exit code as a trail error, not only as a console
line. A run where every fix fails is otherwise indistinguishable in the trail
from a run where the fix pass had nothing to do.

### Already addressed, or addressed in response

The `already_addressed` verdict means the code does what the reviewer asked. It
does not mean their comment was moot, and the two are not the same claim.
Triage reads code context from current HEAD, which already holds whatever the
pass fixed earlier in the same cycle, so a re-run re-triages a thread it fixed
on round one and gets `already_addressed` for it on round two — correctly. What
was wrong was the reply: a flat `Already addressed` told a reviewer their point
needed no action while the paragraph below it cited a commit made after they
made it ([#815](https://github.com/otto-nation/otto-workbench/issues/815)).

So the reply and the summary row ask when the code became true, relative to
when the thread was opened:

| The branch shows | Reply | Summary cell |
|---|---|---|
| a commit on the thread's line, dated after the review comment | `Applied:` … `Fixed in <sha>` | `Fixed in <sha>`, counted with the fixes |
| a commit on that line, dated before the comment | `Already addressed:` … `Addressed in <sha>` | `Already addressed` |
| no commit on that line — the code predates the branch | `Already addressed:` | `Already addressed` |

The commit is the one `git log -L` names for the thread's line, so two threads
on one file get two answers; its committer date is what is compared, because a
rebased fix keeps the author date it was first written at. Either timestamp
missing reads as pre-existing: claiming credit for a fix is the assertion that
needs evidence, and there is none when one side of the comparison cannot be
dated.

`pr comments --finish` reaches this reply from a second direction. A fixed
thread whose commit the resolver cannot cite — a hook rejected the pass's
commit, so nothing was recorded — is routed here for the linkless body
([#827](https://github.com/otto-nation/otto-workbench/issues/827)). The pass
demonstrably acted on that thread, which is what put it in `fixed`, so it keeps
the in-response reading and names no commit: the one the branch offers for that
line predates the comment and cannot be what carried a fix made after it.

### The summary comments are the record, not the state file

The `Review Comments Addressed` comments are what a reviewer reads to confirm
their feedback was accounted for, and they are the one place a cycle is tallied.
A round that still has the last word edits its own summary in place; a round a
reviewer has spoken over posts a new one and links back to the earlier ones. The
record is therefore the *set* of summary comments on the PR, and each rule below
is read against that set rather than against one body — a row is safe when some
comment on the PR still carries it, not only when the newest one does.

Three things follow, and the first two were bugs —
[#714](https://github.com/otto-nation/otto-workbench/issues/714) and
[#712](https://github.com/otto-nation/otto-workbench/issues/712):

**Every outcome is reported, including the ones nobody resolved.** A
`needs_human` thread is the case that took the most operator judgment, so
omitting it is the worst row to lose. It renders as open, with its reason, in
every summary a round posts — an open question a reader has to walk the comment
chain to find is one they will not find. If the operator settled it outside the
tool — answered the thread, fixed it by hand — `--finish` reconciles the
snapshot against GitHub first and the row credits that work instead of reporting
a discussion that already happened.

**A decomposed comment item reconciles through the comment it came from.** An
item split out of a top-level comment has a synthetic id and no review thread,
so there is no thread state to read it from
([#776](https://github.com/otto-nation/otto-workbench/issues/776)). `--finish`
asks the same question of its source instead: a reply of ours further down the
PR that opens `Applied:` / `Already addressed` /
`Suggestion reviewed and determined to be inapplicable` and cites
the source comment's permalink settles the item, exactly as the equivalent reply
on a thread settles a thread. An item that restates an inline thread settles
with that thread, and renders as one row rather than the same point twice under
two outcomes — the thread is the copy that stays, since that is where the reply
lands and where resolution is recorded. An item nothing on GitHub answers still
renders as open and still holds the summary back.

**Rows the local state file cannot account for are carried forward.** State is
per-target and per-worktree, and a round routinely runs without the state that
covered an earlier one: `pr gc`, a pruned state directory, a recreated worktree,
another machine. Overwriting a comment with a body built purely from state then
deletes rounds nobody can recover. So an edit reads its target's body first,
matches it row by row on the thread permalink, and re-emits anything
unaccounted for verbatim, counted as `N carried over` and warned about on the
run. Carrying forward is scoped to the comment being replaced, because that is
the only comment an edit can destroy: a round that posts a new comment takes
nothing away, so it has nothing to carry.

A comment therefore only grows for as long as rounds keep editing it. A row no
later round reproduces keeps its last rendered state rather than vanishing from
the comment holding it — a stale row a reviewer can still read beats a round
nobody can recover.

**An Action cell written by hand is never re-rendered.** Carrying rows forward
protects a row state cannot account for; it cannot protect the Action cell,
because the row key deliberately excludes it — a round changing that cell
(deferred to fixed) has to count as the same row. The Action cell is also the
only cell a person edits, so the two rules once composed into the inverse of the
intent: a hand edit survived exactly as long as local state had lost the thread,
and was overwritten the round state regained it. So each published row is asked
a second question. If its Action cell opens with none of the wordings this
renderer writes, a person wrote it: the published row is re-emitted in the
position its entry would have taken, counted as `N hand-written`, and the run
warns with the cell it kept and the cell it would have written instead. Every
summary comment is searched, not just the newest — a cell edited on round one's
comment is the usual case once round two has posted its own, and the newest
occurrence of a row wins, so restoring a generated cell by hand hands the row
back to the renderer.

The header counts follow the cells. An entry whose row is held drops out of its
own bucket, so a row reading `Superseded — …` no longer sits under a header
reading `1 need discussion` — the contradiction that reopened a question the PR
had closed. Nothing tries to read a classification back out of the prose a human
wrote; the row simply stops being counted as anything but hand-written. Hand a
row back to the renderer by restoring a generated cell on the published comment
with `gh api -X PATCH`.

**A summary that has been answered is reposted, not edited.** GitHub leaves an
edited comment where it was and notifies nobody, so once a reviewer has
commented, submitted a review, or replied on a thread below the summary, editing
it writes the round's outcome somewhere the reader has already scrolled past.
Each round compares the summary's `created_at` against the newest activity that
is not ours — our own thread replies do not count, or the fix pass would trip
the check on itself every round — and posts a fresh comment when it lost the
last word. The fresh body carries the marker, so the next round finds it; the
superseded comment is left untouched as that round's record.

**A fresh summary describes the round that produced it**
([#924](https://github.com/otto-nation/otto-workbench/issues/924)). Restating
every thread the PR ever had made each new comment a complete record and an
unreadable one — the reader could not tell this round's work from what was
settled three rounds ago, and every reviewer was re-notified with mostly stale
content. A row may be left where it was published only when all three hold: some
summary comment on the PR already carries it, the comment this round is writing
is not one of them, and no one but us has spoken on the thread since the newest
summary went up. A thread nobody can date is written rather than skipped, since
"cannot tell" must not read as "settled". The skipped rows are counted in a note
under the table, and the round's own footer links every earlier summary comment
in order, so a reader landing on the newest one can walk the chain back through
the rounds it does not restate.

### Running from a different directory

All global tasks default to running in the current working directory. When your CWD is not the target repo (e.g., running from a Claude Code session rooted in a different project), pass `REPO_DIR`:

```bash
task --global REPO_DIR=/path/to/worktree pr:create -- --no-issue
task --global REPO_DIR=/path/to/worktree commit
```

A related hazard exists one level down, for the AI subprocess rather than the
shell task: a backend CLI inherits the launching process's working directory
unless it is told otherwise. Every `ai_backend` entry point therefore takes a
required `cwd` — see [`ai_backend.py`](ai-libraries.md#ai_backendpy).

### How `pr` decides what a bare token is

`pr` asks a delegate for `--value-flags` — the hidden arity probe answered by
[`tool_parser.py`](ai-libraries.md#tool_parserpy) — before deciding whether a
bare token is the command's target or some other flag's argument. Without it,
`pr comments --reply 3777767789` reads the reply ID as a PR number and swallows
it.

A subcommand with no delegate has no parser to probe, and one of them needs none.
`pr create` shells out to `task pr:create`, whose flags are parsed in bash by
`parse_pr_flags` ([`lib/ai/pr.sh`](../lib/ai/pr.sh)); it always operates on the
current branch, and `task pr:create` has no way to accept a target. So `create` is
listed in `_NO_TARGET_COMMANDS` and the positional scan is skipped for it entirely —
every bare token in `pr create --title "…" --body-file …` belongs to the flag before
it. Mirroring `parse_pr_flags`'s arity in `pr` would have been a third copy of a list
that already exists twice in the file that parses it.

That leaves `_NO_TARGET_COMMANDS` a hand-maintained exclusion, so the commands it does
*not* excuse are guarded: `status`, `fix` and `gc` have no delegate either, and their
scan is arity-blind for the same reason. A test reads `value_taking_options` off each
of their subparsers — the same function the probe answers with — and fails the build
the day one of them declares an option that consumes a value, naming the two ways out:
list the command as taking no target, or give it a delegate to ask.

### Three shared foundations under `ai/`

Three modules exist because the same decision was being re-made at every call
site, and the spread was the bug. Each owns its own reference page:

| Module | Takes over | Reference |
|---|---|---|
| `proc` | Running a subprocess, and what a failure is allowed to say — stderr included. | [`proc.py`](ai-libraries.md#procpy) |
| `timeouts` | How long a subprocess may run, chosen as a tier rather than a number. | [`timeouts.py`](ai-libraries.md#timeoutspy) |
| `git_client` | Invoking `git` — `cwd`, capture, non-zero handling, and per-subcommand config. | [`git_client.py`](ai-libraries.md#git_clientpy) |

They stack: `git_client` sits on `proc`, which requires a `timeouts` tier on
every call. `bin/local/validate-timeouts` enforces the last of those across
`ai/`, so a new subprocess call cannot skip the question.

## Guidelines & Rules

The workbench installs a layered rule system into Claude Code:

- **Global guidelines** ([`ai/guidelines/`](../ai/guidelines/)) — universal coding principles, language-specific rules
- **Tool rules** ([`ai/guidelines/rules/`](../ai/guidelines/rules/)) — path-scoped rules that auto-load based on file type
- **Generated rules** — [`tools.generated.md`](../ai/guidelines/rules/tools.generated.md) and [`git.generated.md`](../ai/guidelines/rules/git.generated.md) are derived from registries and conventions

Rules are symlinked to `~/.claude/rules/` during sync. Add machine-specific rules with:

```bash
claude-rules add <domain> "rule text"    # add a local rule
claude-rules list                        # show all rules
claude-rules status                      # check sync status
```

## Scaffolding a New Project

After cloning a repo, scaffold Claude Code configuration for it:

```bash
otto-workbench ai init          # scaffold .claude/ in the current repo
otto-workbench ai init --force  # re-scaffold an existing project
```

This creates a `.claude/` directory with stack-detected rules and a project anatomy file (file index with token estimates).
