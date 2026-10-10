---
title: AI Automation
description: Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.
---

<!-- doc-budget: 576 -->

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

rtk and worktrunk install through Homebrew where it is available. On a machine
without it, such as an unprivileged Linux user with no writable brew prefix,
they install through mise (`mise use -g rtk`, `mise use -g worktrunk`). When
neither is present, `install_brew_or_mise` first installs mise itself with its
own installer (`https://mise.run`), so a fresh Linux account needs nothing
beforehand. worktrunk is installed by the git component as well, since that
component configures it and `wt-init` runs it.

`ai/setup.sh` also scaffolds `~/.config/task/taskfile.env`, the GitHub PATs `pr create` and `pr describe --post` publish with (`GH_TOKEN`, optional `GH_TOKEN__<ORG>`). See `security-secrets.md` for the two-file secret model.

## What Gets Installed

<!-- include: bin/local/generate-tool-context --emit ai-installs -->

<!-- include: bin/local/generate-tool-context --emit skill-reference -->

<!-- include: bin/local/generate-tool-context --emit lifecycle -->

## Herdr

Selecting `herdr` in `ai/setup.sh` installs [herdr](https://herdr.dev/) with its
own installer (`~/.local/bin/herdr`) and, on every sync, runs `herdr update` and
reinstalls its Pi and Claude Code integrations for whichever harness is
configured. The integrations let herdr resume a Pi or Claude session after its
server restarts; Pi also reports working/blocked/idle directly.

Herdr is an operator tool. Agents get no permission to run it, so it never
competes with `job_start`/`run_in_background` as a way to spawn work.

- Create worktrees with `wt`, not `herdr worktree` — herdr's default
  `~/.herdr/worktrees` sits outside the workbench layout.
- After setup, register a remote machine with
  `herdr machine add <ssh-host> --label <name>`; the reminder prints during
  setup and `otto-workbench ai sync` while none is registered, not during a
  regular `otto-workbench sync`.
- `~/.config/herdr/config.toml` is not managed yet (#1660).

## Configuration

### Usage ledger

Every AI call made through the workbench records what it cost. Query the ledger
with `otto-log stats`:

```bash
otto-log stats                      # last 7 days, grouped by script
otto-log stats --since 24h          # any h/d/m window
otto-log stats --by task            # or: script, model, day, phase
otto-log stats --by day --json      # one JSON object per row
```

What is recorded, and what each column means, is documented on
[`agent/usage.py`](ai-libraries.md#agentusagepy).

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
pairs a runner with a scorer in `ai/lib/eval/scoring_<task>.py`. What a case of
each task holds is documented on [`eval/task.py`](ai-libraries.md#evaltaskpy);
how each is scored, on the task's own module —
[`review`](ai-libraries.md#evalscoring_reviewpy),
[`ci-fix`](ai-libraries.md#evalscoring_cifixpy),
[`skill`](ai-libraries.md#evalscoring_skillpy). The runner, the fixture repo,
and the statistics over repeated runs are shared and know nothing about any one
task.

Landing a case costs a full eval run. `bin/local/validate-eval-baselines` fails
any corpus entry that no baseline in `eval/results/` covers, and
`bin/local/validate-all` discovers it by glob — so a new case is red on pre-push
and in CI from the moment it lands until a baseline records it. That baseline has
to come from a run over the whole corpus: `--save-baselines` rebuilds each
model's file wholesale from the entries of the run it is handed, so
`--entry <new-case> --save-baselines` writes a file holding that one entry and
drops every other. There is no filtered top-up. A name the on-disk baseline
does not yet carry, or a first run under a new backend, is a first floor —
`--save-baselines` records it; `validate-eval-floors` still fails an unfloored
entry on disk. Retire a case by deleting it from `eval/corpus/`, every
`{backend}-{model}.json`, and every stem in `floors.json` together: a floor
with no baseline is dropped, a baseline with no floor is unfloored, a corpus
case with no baseline fails `validate-eval-baselines`. Partial deletion fails
by design.

`--compare` diffs a run against same-backend `{backend}-{model}.json` files
and exits `2` on a regression; `--save-baselines` records the served model and
exits `3` when unresolved or a run never executed; `--seed-floors` rebuilds a
missing `floors.json` from this run's scores, not git history (`best` is the
current values — a decayed run collapses the high-water mark) and warns when
it reseeds. Which metrics gate, and why a dead run is not a score, are on
[`eval/scoring.py`](ai-libraries.md#evalscoringpy). The
[`Eval` workflow](../.github/workflows/eval.yml) runs this weekly and on demand
— not a pull-request check: each run spends real money on real model calls, and
without `ANTHROPIC_API_KEY` it validates the corpus and stops.

### Checking the rules still reach an agent

Every agent the pipeline runs gets its coding rules through an undocumented
`--bare`/`--add-dir` coupling, which can break with no error and no missing file.

```bash
rules-canary            # exit 0 rules arrive, 1 they do not, 2 could not measure
rules-canary --json     # both readings, the delta, and the floor
```

How it measures that, and why it is a difference of two runs, are on
[`eval/rules_canary.py`](ai-libraries.md#evalrules_canarypy). The
[`Eval` workflow](../.github/workflows/eval.yml) runs it ahead of the corpus and
reports it separately — a canary that cannot measure should not cancel the
week's eval.

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

Review, comments and CI fixes, and `already_addressed` verdicts, are checked by
one verify gate before they land or post: [`fix/gate.py`](ai-libraries.md#fixgatepy).

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
| the thread's line no longer means what it did when it was read | `Already addressed:` | `Already addressed` |

The commit is the one `git log -L` names for the thread's line, so two threads
on one file get two answers, and both the line and the date it is judged by are
checked so a rebase cannot invent one — `pr/attribution.py` carries the rules.
Either timestamp missing reads as pre-existing: claiming credit for a fix is the
assertion that needs evidence, and there is none when one side cannot be dated.

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

When your CWD is not the target repo (e.g., running from a Claude Code session rooted in a different project), pass `--repo-dir` (or `--branch`) to `pr`:

```bash
pr create --draft --closes 941 --repo-dir /path/to/worktree
```

A related hazard exists one level down, for the AI subprocess: a backend CLI
inherits the launching process's working directory unless it is told otherwise.
Every `ai_backend` entry point therefore takes a required `cwd` — see
[`agent/backend.py`](ai-libraries.md#agentbackendpy).

### Running a branch's own libraries

The Python entry points take their own pin, since `~/.local/bin/pr` is a symlink into
`main/` and `Path(__file__).resolve()` follows it — see `ai/bin/_libdir.py`:

```bash
WORKBENCH_AI_LIB_DIR=/path/to/worktree pr review --self --fix
```

### How `pr` decides what a bare token is

`pr` reads the delegate's own parser — `value_taking_options` in
[`core/tool_parser.py`](ai-libraries.md#coretool_parserpy) — before deciding whether a
bare token is the command's target or some other flag's argument. Without it,
`pr comments --reply 3777767789` reads the reply ID as a PR number and swallows
it. `PARSER_FACTORIES` in [`ai/lib/cli/dispatch.py`](../ai/lib/cli/dispatch.py)
names each delegate's `build_parser`.

One delegate needs no scan at all. `pr create` declares no positional: its
target comes from the global `--branch`/`--repo-dir`, already resolved into the
context before `cli.pr_create` sees its argv. So `create` declares
`takes_target=False` and the positional scan is skipped for it entirely — every
bare token in `pr create --title "…" --body-file …` belongs to the flag before it,
including a title that happens to be a number.

That leaves `takes_target` a hand-maintained declaration, so the commands it does
*not* excuse are guarded: `status`, `fix` and `gc` have no delegate either, and their
scan is arity-blind for the same reason. A test reads `value_taking_options` off each
of their subparsers — the same function `pr` classifies with — and fails the build the
day one declares an option that consumes a value, naming the two ways out: declare the
command as taking no target, or give it a delegate to read.

### pr batch

`pr batch` rebases, CI-fixes, addresses comments on and self-reviews your open
PRs. Every step is a child `pr` (CI: `ci-check`) in a new session with stdin
closed, and every step runs drafted — the batch is the only thing that pushes.

```bash
pr batch plan --checkout DIR …           # which PRs need which steps (JSON)
pr batch run --checkout DIR … [opts]     # start; NDJSON events, last line run_summary
pr batch run --plan FILE …               # start from a saved plan
pr batch resume [RUN_ID]                 # continue waiting or interrupted
pr batch next [RUN_ID]                   # only what needs action (JSON)
pr batch status [RUN_ID]                 # the run report (JSON)
pr batch status [RUN_ID] --full          # the raw run state
pr batch status [RUN_ID] --decision ID   # one decision: full payload and log tail
pr batch resolve RUN_ID DECISION_ID --action A [--reason/--body-file/--commit]
pr batch cancel [RUN_ID] [--kill]
```

Steps run in the order `rebase`, `ci`, `comments`, `review`, each only where
needed (`run` plans for itself; `run --help` has recipes). `rebase` is needed
when the branch is behind its PR's base, from refs in a private `refs/pr-batch/`
namespace (GitHub merge state fallback; `UNKNOWN` and `DIRTY` count as needed).
`ci` runs `ci-check --fix --no-rebase --head-sha <planned remote head>` (not
`pr ci`) when the rollup failed, with `--wait` if it was still running. Fork
heads skip both. A stacked PR waits for its base's publish; `resume` retries
`stacked_on` once that base is terminal. One fetching rebase or CI step per
repo at a time (a waiting CI step excepted). No start in a dirty worktree
(`dirty_worktree`), except a rebase resuming its own paused replay.

An item that closes with drafted work opens one `publish` decision. Publish
fetches the branch and refuses (`failed`) with `fetch_failed`, `remote_moved`,
`not_comparable`, `not_incorporated`, or `not_incorporated_remote`. Otherwise
it fast-forwards, or force-pushes with `pr rebase --push-only --expect
<planned head>`, then posts comment replies only when the comments step
drafted or items were tracked. `force-publish` answers only
`not_incorporated_remote` for the listed commits — a new remote commit refuses
again. `--auto-publish STEPS` resolves that decision when the item closes with
no open decision and every drafted step is listed and finished `done`.
`--watch-ci` re-checks CI once after a push and reopens on red; `run_finished`
lists `ci_not_rechecked`.

A step that needs a person opens one `step_review` whose `evidence` is
`open_findings`, `checks_unverified` (`Fix-Checks:` `red`/`timed_out`/`error`/`partial`,
or `unreadable` when the step's commit-range `git log` failed; a commit with
no trailer is not evidence), `ci_unfixed`, `one_sided`, or `stacked_on`.
Actions: `accept`, `retry`, `skip-step`, `undo` (rebase:
`git reset --hard` to the pre-rebase head). Kinds and actions are tabled in
[`batch/resolve.py`](ai-libraries.md#batchresolvepy).

`pr batch status` prints one compact report:

- `run`: `id`, `status`, `active` (a scheduler holds the run's lock), `exit_hint` and a one-line `hint`. `exit_hint` is what `run`/`resume` exit with: 0 once the run is finished (`done` or `cancelled`), 10 while it waits on you, 1 when it stopped without settling (resume it), and null while a scheduler holds the run.
- `counts`: items by status, and open decisions.
- `items`: non-terminal items only — worktree, branch, `remote_sha`, `pre_rebase_head`, `stacked_on`, `base_ref`, and step statuses.
- `next`: one entry per open decision.

`pr batch next` prints the same report without `items`.

Each `next` entry carries:

- `why`: a classified sentence.
- `prep`: any work outside `pr` it needs first — the conflicted files to resolve, the review file and its must/should/nit counts (declined findings are left out of those and counted apart as `declined`), the remote commits to check, or a thread excerpt.
- `worktree`.
- `actions`, and `commands`: one copy-paste `pr batch resolve` per action. A required input is shown as a placeholder, an optional one in brackets.
- `payload`: a small kind-specific part of the decision's payload.
- `log` and `session_log` paths.

Log tails appear only under `--decision`. `next` is empty in two cases:

- while a scheduler holds the run — wait for its `run_summary`;
- once the run is `done` or `cancelled`.

A `failed` decision's `reason` is one of the classified reasons listed in
[`batch/outcomes.py`](ai-libraries.md#batchoutcomespy).

A failed step names its `log`, plus `session_log` when a review left one. A failed publish command names its `logs/<owner>__<repo>-<pr>-publish-<n>.log` and keeps the line that showed the reason as `detail` (absent for `error`). A publish refusal runs no command, so it has no log; its `detail` says why.

Step stderr stays in the run's `logs/`; `run --verbose` and `resume --verbose` also stream it as `step_log` events. `decision_created` carries only `run`, `item`, `decision`, `decision_kind`, `why` and `commands`. `item` is the same value as `next[].pr`; the kind is `decision_kind` because the event wrapper's own `kind` is the event type. The last line of every `run` or `resume` that settles is `run_summary`, holding the same `run`, `counts` and `next` as `pr batch next`.

A run with open decisions exits **10**. That means it is waiting on you, not that it failed: a job runner reports it as a failure, so read the `run_summary` line rather than the exit status. `resume` continues the run and clears a pending cancel. `open-chat` is UI-only.

Admission starts a step when free memory
minus `batch.mem_reserve` covers its observed peak and CPU/memory pressure stay
under `batch.cpu_pressure_max` / `batch.mem_pressure_max`, always admitting
one. `--pool` and `batch.pool_max` (default 2) cap concurrency; hosts with no
pressure metrics (macOS) use `batch.pool_default` (1). State lives under
`~/.local/state/workbench/batch/<run-id>/` (`state.json` is authoritative).

### The Pi package the sync declares, and who gets it

Pi reads *and writes* `~/.pi/agent/settings.json` — `pi install`, `pi config` and
Ctrl+S in `/model` all land there — so `step_pi_settings` ([`ai/pi/steps.sh`](../ai/pi/steps.sh))
merges the workbench's template into that file rather than copying over it. Scalar
keys are seeds: one the live file already carries stays as whatever set it first,
which also means a changed template default never reaches a machine that already
has the key. Delete the key there to be re-seeded. A model id `pi --list-models`
does not list for the default provider is a sync warning, not a failure. That
check (`step_pi_models`) runs after the package refresh and asks the user's own pi
from `$HOME`, so neither a stale extension clone nor a repo's pinned pi reads as a
wrong id. A default provider with no rows at all gets one warning that it did not
load: the Vertex extensions skip registration when the shell has no
`GOOGLE_CLOUD_PROJECT` or ADC file, which is usually a shell started before
`~/.env.local` set them.

`packages` is reconciled instead, because a list gains an entry without
displacing one. Before declaring one, the sync asks GitHub whether this machine
can fetch it — `gh api repos/<owner>/<repo>`. Three answers, two of which act:

| Verdict | What the sync does |
|---|---|
| the repo answers | declares the package |
| a definitive 404 | withdraws it, so Pi stops retrying a clone it cannot complete |
| no `gh`, no auth, no network | leaves the entry however the live file has it |

The third row is why the check is not a boolean: a sync run offline must neither
install a package it could not verify nor strip one that already works. It asks
about the repo rather than the owning org because a user account's public repo
has no org, and a restricted repo in one this machine belongs to still cannot be
cloned. What extensions expose to a run is decided in
[`agent/backend_pi.py`](ai-libraries.md#agentbackend_pipy); how an existing entry
is recognised is one [`sync-settings.jq`](../ai/pi/sync-settings.jq) documents.

### Five shared foundations under `ai/`

Five modules exist because the same decision was being re-made at every call
site, and the spread was the bug. Each owns its own reference page:

| Module | Takes over | Reference |
|---|---|---|
| `proc` | Running a subprocess, and what a failure is allowed to say — stderr included. | [`core/proc.py`](ai-libraries.md#coreprocpy) |
| `timeouts` | How long a subprocess may run, chosen as a tier rather than a number. | [`core/timeouts.py`](ai-libraries.md#coretimeoutspy) |
| `git_client` | Invoking `git` — `cwd`, capture, non-zero handling, and per-subcommand config. | [`git/client.py`](ai-libraries.md#gitclientpy) |
| `push` | Pushing, and asking the remote whether it actually took it. | [`git/push.py`](ai-libraries.md#gitpushpy) |
| `land` | Committing a pass's work and pushing it, as one act with one result. | [`git/land.py`](ai-libraries.md#gitlandpy) |

They stack: `land` sits on `push` and `git_client`, which sit on `proc`, which
requires a `timeouts` tier on every call — `bin/local/validate-timeouts` enforces
that one across `ai/`, so a new subprocess call cannot skip the question. `pr create`
pushes through `push` by way of `pr.branch_sync`, and a push typed by hand is recorded by the global `pre-push` hook for `pr` to reconcile.

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

This creates a `.claude/` directory with stack-detected rules and a project anatomy file (file index with token estimates), plus a root `AGENTS.md` — the file every harness reads (Claude Code natively from 2.1.277, which otto-workbench's AGENTS.md support assumes; `ai sync` warns on an older one). A repo still on `CLAUDE.md` keeps working: `ai init` leaves it alone and suggests `git mv CLAUDE.md AGENTS.md`, and the SessionStart hook and `ai sync` repeat the suggestion. Nothing renames it for you.
