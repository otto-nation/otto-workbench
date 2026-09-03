---
title: AI Automation
description: Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.
---

<!-- doc-budget: 437 -->

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

Prompts for confirmation at each step. Safe to re-run. This installs Claude Code configuration, rules, and agents, plus skills — shared between Claude Code (`~/.claude/skills/`) and Pi (`~/.agents/skills/`) from the one `ai/skills/` tree.

After setup, configure the AI tool for global task automation:

```bash
task --global ai:setup
```

This creates `~/.config/task/taskfile.env` with:
- `AI_COMMAND` — which AI tool to use (e.g., `claude -p --output-format json --agent ci-cd`)
- `GH_TOKEN` — GitHub PAT for PR automation (fine-grained, scoped to specific repos)
- `ANTHROPIC_API_KEY` — optional, for isolating automation API usage

## What Gets Installed

<!-- include: bin/local/generate-tool-context --emit ai-installs -->

<!-- include: bin/local/generate-tool-context --emit skill-reference -->

<!-- include: bin/local/generate-tool-context --emit lifecycle -->

## Task Automation

Use `--global` to run tasks from `~/.config/task/` rather than a local project Taskfile.

<!-- include: bin/local/generate-tool-context --emit tasks-block -->

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

### A rebase renames the held commit, it does not unpublish it

A fix pass that holds its push records the SHA it committed, and the run that
clears the hold often comes *after* a rebase — `pr rebase --fix` is what a
supersession warning tells the operator to run. The rewrite leaves that SHA
resolvable as an object and contained by no branch, the one shape "has this been
pushed?" answers wrong
([#952](https://github.com/otto-nation/otto-workbench/issues/952)). So `--finish`
follows the rename first, matching an orphan to its replay on patch id — every
pass commits under one static subject, so content is the only field that tells
two of them apart — and cites the branch's own history from there. Nothing
clears on a guess: a commit still on the branch is unpushed for the ordinary
reason, and an orphan with no replay, or with two, holds too and says how to
recover. Re-running `--fix` is not the way; it discards reviewed replies.

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
tool, `--finish` reconciles the snapshot against GitHub first and grades what
it finds: a reply of ours naming the verdict credits a fix, a bare resolution
records `settled_elsewhere` — no fixed tally, no commit, nothing owed.

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

### The Pi package the sync declares, and who gets it

Pi reads *and writes* `~/.pi/agent/settings.json` — `pi install`, `pi config` and
Ctrl+S in `/model` all land there — so `step_pi_settings`
([`ai/pi/steps.sh`](../ai/pi/steps.sh)) merges the workbench's template into that
file rather than copying over it. Scalar keys are seeds: one the live file
already carries stays as whatever set it first, which also means a changed
template default never reaches a machine that already has the key. Delete the key
there to be re-seeded.

`packages` is reconciled instead, because a list gains an entry without
displacing one. The template declares `git:github.com/usemaximum/pi-extensions`,
private to its org, so the sync asks GitHub whether this machine's account is an
active member first. The answer is three-valued and only two of the three act:

| Verdict | What the sync does |
|---|---|
| active member | declares the package |
| refused, or an invitation still pending | withdraws it, so Pi stops retrying a clone it cannot complete |
| no `gh`, no auth, no network | leaves the entry however the live file has it |

The third row is why the check is not a boolean: a sync run offline must neither
install a package it could not verify nor strip one that already works. What the
extensions then expose to a run is a separate decision, made in
[`ai_backend_pi.py`](ai-libraries.md#ai_backend_pipy); how an entry already in
the file is recognised is one [`sync-settings.jq`](../ai/pi/sync-settings.jq)
documents at the top.

### Five shared foundations under `ai/`

Five modules exist because the same decision was being re-made at every call
site, and the spread was the bug. Each owns its own reference page:

| Module | Takes over | Reference |
|---|---|---|
| `proc` | Running a subprocess, and what a failure is allowed to say — stderr included. | [`proc.py`](ai-libraries.md#procpy) |
| `timeouts` | How long a subprocess may run, chosen as a tier rather than a number. | [`timeouts.py`](ai-libraries.md#timeoutspy) |
| `git_client` | Invoking `git` — `cwd`, capture, non-zero handling, and per-subcommand config. | [`git_client.py`](ai-libraries.md#git_clientpy) |
| `push` | Pushing, and asking the remote whether it actually took it. | [`push.py`](ai-libraries.md#pushpy) |
| `land` | Committing a pass's work and pushing it, as one act with one result. | [`land.py`](ai-libraries.md#landpy) |

They stack: `land` sits on `push` and `git_client`, which sit on `proc`, which
requires a `timeouts` tier on every call — `bin/local/validate-timeouts` enforces
that one across `ai/`, so a new subprocess call cannot skip the question. The bash
half of `pr:create` reaches `push` through its CLI rather than reimplementing it,
and a push typed by hand is recorded by the global `pre-push` hook for `pr` to reconcile.

## Guidelines & Rules

The workbench resolves a layered rule system, which every harness it installs
reads from. A later layer wins a name, and the operator's is last:

- **Repo defaults** ([`ai/guidelines/rules/`](../ai/guidelines/rules/)) — path-scoped rules that auto-load based on file type, plus always-on, harness-neutral rules. [`tools.generated.md`](../ai/guidelines/rules/tools.generated.md) and [`git.generated.md`](../ai/guidelines/rules/git.generated.md) are derived from registries and conventions
- **Generated** (`~/.local/state/workbench/rules/`) — `workbench.md`, rewritten on every sync with this machine's paths baked in
- **Operator overrides** (`~/.config/workbench/overrides/ai/guidelines/rules/`) — a same-named `<domain>.md` replaces the layer below, a `<domain>.local.md` is carried alongside it, and a `<domain>.disabled` sentinel suppresses it entirely

`resolve_rules` ([`lib/files.sh`](../lib/files.sh)) merges the three, and each
harness installs that one set the way it wants it — which is what lets a machine
with only one of them installed still get its rules. Add machine-specific rules
with `workbench-rules add <domain> "rule text"`; `list` and `status` show what
this machine has added.

`step_claude_rules` ([`ai/claude/steps.sh`](../ai/claude/steps.sh)) symlinks the
merged set into `~/.claude/rules/`. Pi reads one context file per directory
rather than a rules directory, so `step_pi_guidelines`
([`ai/pi/steps.sh`](../ai/pi/steps.sh)) concatenates the same set — minus
anything `paths:`-scoped or `harness:`-excluded from `pi` — behind the
`ai/pi/AGENTS.head.md` preamble, into `~/.pi/agent/AGENTS.md`.
Write `~/.pi/agent/AGENTS.override.md` to replace it; the workbench never
touches that file. See `rules-authoring.md` § Which harnesses a rule reaches
for the full frontmatter table.

## Scaffolding a New Project

After cloning a repo, scaffold Claude Code configuration for it:

```bash
otto-workbench ai init          # scaffold .claude/ in the current repo
otto-workbench ai init --force  # re-scaffold an existing project
```

This creates a `.claude/` directory with stack-detected rules and a project anatomy file (file index with token estimates), plus a root `CLAUDE.md` — the file every harness reads.
