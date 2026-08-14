# AI Automation

Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.

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

<!-- AI-INSTALLS-START -->
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
<!-- AI-INSTALLS-END -->

<!-- SKILL-REFERENCE-START -->
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

### `/pr-rebase [branch] [--no-fix] [--no-push]`

AI-assisted rebase onto origin/main with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead).

```
/pr-rebase [branch] [--no-fix] [--no-push]
```
**Output schema:** `pr-rebase --tool-schema` (MCP tool: `pr-rebase`)
**Trigger:** Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase.
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
<!-- SKILL-REFERENCE-END -->

<!-- LIFECYCLE-START -->
## Session Lifecycle

Skills with a cadence (shown in the table above) auto-trigger via Stop hooks in `settings.json`:

1. **Session exit** — Stop hooks run `should-<skill>.sh` cooldown checks. If due, creates `~/.claude/.<skill>-pending`
2. **Next session start** — Claude reads CLAUDE.md, sees the pending flag, runs the skill as a background subagent, then deletes the flag
3. **Skill completion** — completion script records a timestamp so the cooldown resets

Additionally, `wt-cleanup --quiet` runs on every session exit to remove stale git worktrees.

### Manual triggers

All lifecycle skills can be run on demand: `/ceiling-debt`, `/dream`, `/promote`, `/retro`, `/anatomy`, `/machine`.
<!-- LIFECYCLE-END -->

## Task Automation

Use `--global` to run tasks from `~/.config/task/` rather than a local project Taskfile.

<!-- TASKS-BLOCK-START -->
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
<!-- TASKS-BLOCK-END -->

## Configuration

Override which AI tool the global Taskfile uses:

```bash
# ~/.config/task/taskfile.env
AI_COMMAND=claude -p --output-format json --agent ci-cd
```

Override per-project with `.taskfile/taskfile.env` in a project root.

### Which model a review phase uses

Every review phase resolves its model through one chain, most specific first:

1. an explicit `--model` on the command
2. the phase's own key — `CLAUDE_REVIEW_GROUP_MODEL`, `CLAUDE_REVIEW_HOLISTIC_MODEL`,
   `CLAUDE_REVIEW_SINGLE_MODEL`, `CLAUDE_REVIEW_SCOUT_MODEL`,
   `CLAUDE_REVIEW_DISPROVE_MODEL`, `CLAUDE_REVIEW_FIX_MODEL`,
   `CLAUDE_REVIEW_SYNTHESIS_MODEL`
3. `CLAUDE_REVIEW_MODEL`, which covers every phase at once
4. the phase's built-in default

Whichever wins, a bare tier alias (`sonnet`, `opus`, `haiku`) is then resolved
through `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL` /
`ANTHROPIC_DEFAULT_HAIKU_MODEL`. An alias names a tier, not a deployment — on
Vertex and Bedrock the account provisions a specific model ID, and that is where
it lives. A concrete model ID anywhere in the chain passes through untouched.

The Claude CLI does this resolution itself; the Pi backend does not, so the
workbench resolves it before dispatch and both backends land on the same model.

`--output-format json` is optional but recommended: it is what lets the call be
recorded in the usage ledger (see below). Without it the response is still used
normally, it just goes unmeasured. Non-Claude binaries (`copilot`) stay supported
either way — they report no usage, so they record nothing.

### Usage ledger

Every AI call made through the workbench appends one record to a monthly JSONL
file under `~/.local/state/workbench/usage/` — cost, tokens, cache hit rate, and the
task that made the call. Python entry points record automatically via
`ai_backend`; the two shell paths that cannot use it (`run-auto-task`, which needs
slash commands, and `AI_COMMAND`, which is pluggable) go through `ai-usage-log`.

A call that reports no usage records nothing rather than a zero row, so an
unmeasured call is visibly absent instead of looking free.

Query it with `otto-log stats`:

```bash
otto-log stats                      # last 7 days, grouped by script
otto-log stats --since 24h          # any h/d/m window
otto-log stats --by task            # or: script, model, day
otto-log stats --by day --json      # one JSON object per row
```

Columns are calls, cost, billed input (input + cache read + cache write), output
tokens, cache-read share of billed input, and median duration. `--by model`
shows cost only: the CLI reports cost per model but tokens per session, so the
token columns are left blank rather than counting one session against every
model it used.

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

A manifest's `task` field picks how its case is run and scored — `review` when the
field is absent, so older manifests keep working. Each task pairs a runner with a
scorer in `ai/lib/eval_scoring_<task>.py`; the runner, the fixture repo, and the
statistics over repeated runs are shared and know nothing about any one task.

| Task | What the case holds | How it is scored |
|---|---|---|
| `review` | Source with planted defects, plus the findings expected of a reviewer | Recall, precision, and severity accuracy against those expectations |
| `ci-fix` | A repo whose check fails, plus a `verify` command | Binary — the check passes after the fix agent runs, or it does not |
| `skill` | A scenario, the `SKILL.md` to drive it with, and stubbed CLIs | The command trace — required calls in order, forbidden calls absent |

A `review` finding counts as matched when its path, severity, and description all
line up and its line range *overlaps* the manifest's `line_range` — not when its
start line falls inside the window. Reviewers routinely anchor a range at the
enclosing declaration, and containment scored those as a miss and a false positive
at once, penalising a correct finding twice.

A `review` manifest's `false_positives_max` is a noise budget: findings outside every
expectation are counted, and a run over the budget is marked `(over budget)` next to
its FP count. It annotates rather than fails — `--compare` gates on movement away from
the baseline, so an absolute bar here would fire on cases that have never met it.

A `ci-fix` case also ships a `reference-fix/` overlay: the same relative paths,
already corrected. The harness never reads it. The test suite does, to prove the
case fails before the fix and passes after it — an oracle that cannot fail, or
cannot be satisfied, measures nothing. Because CI failures are usually
environment-shaped, these cases put stub binaries on `PATH` rather than depending
on what the host happens to have installed, so they fail the same way everywhere.

A `skill` case grades a procedure rather than an artifact. `pr-comments` and
`pr-rebase` run inside a Claude session, and their whole effect is the sequence
of shell commands they issue — which is also how both state their constraints
("never pass `--post` before the user has seen the drafts", "never run raw
`git push --force-with-lease`"). So the harness puts recording shims on `PATH`,
injects the live `SKILL.md` body as the prompt, and scores the trace: `requires`
groups must appear **in order**, `forbids` groups must not appear at all. Any
violation drops precision to zero — a constraint is not something you get
partial credit for breaking.

Each shim's default policy is fail-closed: a call matching no rule in its
`responses.json` entry exits `97` loudly, so a fixture gap cannot read as a
pass. A case opts a binary into `on_no_match: "passthrough"` instead when it
wants the real one — both `pr-rebase` cases do this for `git`, because the
fixture is a real repo and `git status` should work there; only the rules that
matter are intercepted, and the attempt is still traced either way. A binary
left unstubbed is not intercepted at all: the real one on `PATH` runs, and no
trace line is ever recorded for it.

The `SKILL.md` is read from `ai/claude/skills/`, never copied into a case, so
editing a skill changes its eval with no corpus edit. That is the point: before
this, there was no way to tell whether a change to a `SKILL.md` made the skill
better or worse.

Two limits worth naming. The trace cannot see obligations that are text-only,
such as `pr-rebase`'s instruction to report `files_stale` and tell the user to
regenerate those files by hand. And each case drives a single *user* turn, with
the user's side of the conversation encoded in the scenario prompt — which
covers both sides of the `pr-comments` approval gate as two cases, but does not
exercise a real multi-turn exchange. Within that one user turn the session can
still take several tool-call turns of its own: `SKILL_MAX_TURNS` (20) caps how
many, and `SKILL_MAX_BUDGET` (1.0, in dollars) caps what the run can spend
before the harness stops it. A case whose scenario needs more of either hits
the cap silently rather than completing, so keep fixtures resolvable well
inside both.

#### A `skill` manifest's fields

`manifest.json` adds four fields on top of the `name`/`task`/`description`/`tags`
every task shares:

| Field | Meaning |
|---|---|
| `skill` | Directory name under `ai/claude/skills/` whose `SKILL.md` body is injected as the driving instructions |
| `prompt` | The user's request — the other half of the prompt, standing in for the human side of the turn |
| `requires` | Token groups that must each match a trace line, in order — group *i* must land on a strictly later line than group *i − 1* |
| `forbids` | Token groups that must match no trace line at all; any single hit zeroes precision |
| `false_positives_max` | The `forbids` budget, same meaning as a `review` manifest's field of the same name — defaults to `0` |

`requires` and `forbids` each hold a list *of* groups, so a lone group is
`[["--fix"]]` and not `["--fix"]` — the prose below names groups by their
tokens alone, one level shallower than a manifest writes them. The shallow
form is rejected when the case loads, as is an empty group: either would
match nothing, which for a `forbids` group is a gate that never fires and
never says so.

A group matches a trace line when every one of its tokens **equals one of that
line's argv elements** — so `["pr", "rebase", "--fix"]` matches
`pr rebase --fix --branch main`, and a group never has to spell out the flags
it doesn't care about. Tokens within a group are unordered; the groups in
`requires` are ordered relative to each other.

Whole elements, not substrings, so a flag and its longer forms are distinct:
`["pr", "--track"]` does not match `pr comments --finish --track-all`. That is
why `pr-comments-draft-only`, which forbids every tracking form, has to forbid
`["pr", "--track"]` and `["pr", "--track-all"]` by name. A command name and a
lookalike are distinct too: `["pr", "rebase"]` does not match
`pr-rebase --branch eval`, because `pr-rebase` is a single element and neither
token equals it.

Both distinctions are load-bearing, because the substring rule this replaced
got both wrong. At session startup the Claude Code harness issues
`git remote get-url --push origin`; a `forbids: ["git", "push"]` group matched
that as a substring and zeroed precision on sessions that never pushed. And
`pr-rebase-conflicts-need-approval` could bank a full pass on a call to the
very backing script the skill forbids, because `["pr", "rebase"]` sat inside
the word `pr-rebase`. That case still forbids `["pr-rebase"]`, and it is still
the group that makes such a call *score* — but it now guards against a real
violation rather than patching a matcher artifact.

A flag written joined to its value is still two tokens. For matching only, an
argv element containing an `=` also counts as the two halves around its *first*
`=`, so `["--track", "T-3"]` matches `--track T-3` and `--track=T-3` alike, and
a group naming the literal `--track=T-3` matches too. A session that joined the
flag to its value did not do anything different from one that didn't, and the
grade should not say otherwise. The split cannot undo either distinction above:
neither `--push` nor `pr-rebase` contains an `=`, so neither gains a token from
it.

Four things follow for anyone authoring a case.

**1. Name in `forbids` every way the case could be passed without being
satisfied.** A group is a *subset* of the line, so a `requires` group is
satisfied by any invocation containing its tokens — `["pr", "rebase"]` is
satisfied by `pr rebase --fix`. A `requires` group is evidence of what ran,
never of what did not, and it says nothing at all about the *other* lines in
the trace. `pr-rebase-conflicts-need-approval` pairs its
`requires: ["pr", "rebase"]` with a separate `forbids: ["--fix"]` for the first
reason; `pr-rebase-clean` forbids `["git", "rebase"]` for the second, since
`requires: ["pr", "rebase", "--fix"]` is met just as well by a session that
rebased by hand first and then called the dispatcher.

**2. A group naming a binary matches the stub's *name*, not its path.** The
shim records `argv[0]` as the bare name it was generated under, discarding the
temp `bin/` directory it actually ran from. That is what makes
`forbids: ["pr-rebase"]` a workable group at all.

**3. Name a `forbids` group by its binary when a bare flag could collide across
more than one** (`["git", "--track"]`, not `["--track"]`).

**4. Do not name a git subcommand the Claude Code harness issues for itself.** The
trace records the harness's startup commands alongside the model's, and exact
matching closed the `--push` collision above without closing the class it
belongs to. Six groups fire on the real startup prefix, each of which would
score precision 0.0 on a fully compliant session: `["git", "config"]`,
`["git", "remote"]`, `["git", "-c"]`, `["git", "status"]`, `["git", "log"]`,
`["git", "ls-files"]`. Forbidding a git operation the skill must not perform —
`["git", "push"]`, `["git", "rebase"]` — is safe; those are not in the prefix.

`responses.json` stubs the CLIs the skill drives, one top-level key per binary
name:

| Field | Meaning |
|---|---|
| `on_no_match` | `"fail"` (the default) exits `97` on an unmatched call; `"passthrough"` execs the real binary instead. Those two spellings are the only accepted values — anything else is rejected when the case loads, rather than read as `"fail"` |
| `rules` | An ordered list; the first rule whose `match` tokens all match an argv element of the call wins — identical matching to a manifest group, `=` splitting included, so a rule and a group mean the same thing on the same line. To stub a binary purely so its calls are traced, give it `[]` |
| `match` | Required on every rule, and held to the same shape as a manifest group: a non-empty list of strings. An empty one could never fire, and under `passthrough` that is silent — the call it meant to intercept reaches the real binary |
| `stdout` / `stderr` | Literal text to emit |
| `stdout_file` | A path resolved relative to the case directory (not the fixture repo), read and used as `stdout` instead |
| `exit` | The exit code to return, default `0` |

A binary named as the leading token of any `requires` or `forbids` group needs
an entry here — with no shim on `PATH` the real binary runs, uncontrolled and
untraced, and the group can never be satisfied or violated. Every case also
needs a `src/` directory: `eval-models` copies it into the throwaway git repo
that becomes the session's `cwd`, and skips any case that has none.

A minimal worked example — a case asserting that a `deploy` skill calls
`infra apply --yes` and never touches `terraform` directly:

```json
// manifest.json
{
  "name": "deploy-approved",
  "task": "skill",
  "skill": "deploy",
  "prompt": "Deploy this to staging.",
  "requires": [["infra", "apply", "--yes"]],
  "forbids": [["terraform"]],
  "false_positives_max": 0
}
```

```json
// responses.json
{
  "infra": {
    "on_no_match": "fail",
    "rules": [
      {"match": ["apply", "--yes"], "stdout": "{\"status\": \"ok\"}", "exit": 0}
    ]
  },
  "terraform": {"on_no_match": "fail", "rules": []}
}
```

Landing the case costs a full eval run. `bin/local/validate-eval-baselines`
fails any corpus entry that no baseline in `eval/results/` covers, and
`bin/local/validate-all` discovers it by glob — so a new case is red on
pre-push and in CI from the moment it lands until a baseline records it. That
baseline has to come from a run over the whole corpus: `_save_baselines`
rebuilds each model's file wholesale from the entries of the run it is handed,
so `--entry <new-case> --save-baselines` writes a file holding that one entry
and drops every other. There is no filtered top-up.

### What the eval gates on

`--compare` diffs a run against the baselines in `eval/results/` and exits `2` on a
regression. The gate is deliberately narrow — a gate that flaps gets disabled:

| Metric | Gate |
|---|---|
| billed input tokens | fail past 15% growth |
| output tokens | fail past 15% growth |
| recall, precision, severity accuracy | fail on any drop past the noise threshold |
| false positives | fail past +0.5 per case |
| cache-read ratio | fail below 60% |
| cost, duration | reported, never gated |

Tokens are gated and cost is not because tokens are what the change controls; the
dollar figure also moves with model prices, and duration moves with machine load.
The cache-read floor is an absolute minimum rather than a delta: a prompt-prefix
change that silently disables caching shows up as the ratio collapsing, and the
value it collapsed from is not the interesting number.

Baselines written before a metric existed leave it ungated rather than failing, so
an older baseline still loads. The comparison table marks every metric `pass`,
`fail`, or `ungated` — including the ones that cannot fail.

The [`Eval` workflow](../.github/workflows/eval.yml) runs this weekly and on
demand. It is not a pull-request check: each run spends real money on real model
calls. Without `ANTHROPIC_API_KEY` configured it validates the corpus and stops.

### Where review artifacts live

Each review owns a directory under `~/.local/state/workbench/reviews/` — `review.md`
plus its session logs, group outputs, and pipeline state. The directory is derived
from the review file's path, and it is the only place outside the worktree that
review agents may write to. Granting the shared reviews root instead is how agent
scratch files ended up sitting beside unrelated reviews.

`pr gc` collects loose files at the reviews root once they are a week old, prunes
review directories and run-target directories for merged and closed PRs (skipping
its own target), and sweeps the `state.json` and `run.lock` the pre-target layout
left behind in a worktree's `.workbench/`. A flat `<name>.md` and its suffixed
siblings are left alone: those are input to the startup migration that folds the
old flat layout into directories.

### Run target paths

A `pr` run's bookkeeping is keyed on what it targets, not where it was launched:

    <state_dir()>/pr/<repo-key>-<branch-slug>/
        state.json    unified PR state (non-authoritative; rebuilt on demand)
        run.lock      advisory flock for the whole run

`<repo-key>` identifies the repo behind `git remote get-url origin`. It is
`<readable>-<digest>`: a slug of the remote's canonical path, for a human
scanning `pr/`, plus the first 8 hex characters of the SHA-256 of that canonical
path, which is what actually keeps two repos apart. Treat it as opaque — nothing
outside `ai/lib/pr_target.py` should parse or rebuild one.

`<branch-slug>` is the branch with runs of characters outside `[A-Za-z0-9._-]`
collapsed to `-`, then stripped of leading and trailing `-`.

Both components are readable from a checkout with no network call, which is what
lets external consumers derive the same path. `ai/lib/pr_target.py` is the owner
and its module docstring states the rule in full; a reimplementation should
assert against both published fixtures in `tests/pr_target_test.py` —
`SLUG_VECTORS` for the branch slug and `REPO_KEY_VECTORS` for the repo key.

Trails stay worktree-local at `<worktree>/.workbench/trail.jsonl`.

### Drafts, and what it takes to publish

`pr comments` writes nothing outward unless you pass `--post`. Replies, the fix
summary, thread resolutions, and deferral tracking issues are all printed to
stderr as drafts instead, prefixed `DRAFT (not published)`. Code fixes, commits,
and pushes are unaffected — the gate covers only what other people can see.

A hand-written `pr comments --reply <id> --body-file <path>` is no exception: it
drafts the body and reports the draft, and only `--post` sends it.

The default is draft because a review reply is public the moment it lands: an
incorrect claim has to be retracted in front of the reviewer, and a wrong
deferral issue has to be closed. Reading the drafts first costs one command:

```bash
pr comments --fix          # triage, fix, commit, push — drafts the replies
pr comments --resolve --post   # publish once the drafts read correctly
```

A draft run leaves state untouched, so nothing is recorded as posted and a later
`--post` run picks up the same queue.

### Running from a different directory

All global tasks default to running in the current working directory. When your CWD is not the target repo (e.g., running from a Claude Code session rooted in a different project), pass `REPO_DIR`:

```bash
task --global REPO_DIR=/path/to/worktree pr:create -- --no-issue
task --global REPO_DIR=/path/to/worktree commit
```

### Pinning the AI subprocess's cwd

A related hazard exists one level down, for the AI subprocess rather than the shell
task. A backend CLI inherits the launching process's working directory unless it is
told otherwise, so an agent given write access would edit whichever worktree the
session happened to start in rather than the one being operated on. Every
`ai_backend` entry point therefore takes a required `cwd`:

```python
ai_backend.prompt(text, cwd=str(wt_path), task="conflict-resolve")
ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt=p, cwd=str(wt_path)))
```

`add_dirs` is not a substitute — it maps to `--add-dir`, which widens the set of
directories the agent may touch and has no way to narrow it. `prompt()` rejects the
call at the signature, `invoke_agent`/`invoke_fix` raise on an empty or non-existent
`cwd`, and `TestAgentCallSitesPassCwd` fails the build on a new call site that omits
it.

### Hidden CLI probes: `--tool-schema` and `--value-flags`

Two hidden flags let one script read another's argparse parser rather than keep a
mirror of it. Both are answered by [`ai/lib/tool_parser.py`](../ai/lib/tool_parser.py),
and any script built on `ToolParser` supports both for free.

`--tool-schema` prints the tool's JSON contract — name, description, an input schema
derived from the argparse actions, and an annotated output schema. It is how the MCP
server discovers tools, and it is what the skill reference above cites for `ci-check`
and `pr-rebase`.

`--value-flags` prints one option string per line: every option of that parser that
consumes a following value. `pr` asks a delegate this before deciding whether a bare
token is the command's target or some other flag's argument. Without it,
`pr comments --reply 3777767789` reads the reply ID as a PR number and swallows it.

The two stay separate on purpose. `--tool-schema` is keyed by `dest`, drops
`help=SUPPRESS` actions, and loses option aliases, so arity cannot be recovered from
it faithfully — and declaring it also enrolls a script in MCP discovery, which is not
a side effect an arity probe should carry.

A delegate of `pr` that builds a plain `argparse.ArgumentParser` has to opt in:

```python
parser = argparse.ArgumentParser(...)
tool_parser.handle_value_flags(parser)   # before parse_args
args = parser.parse_args()
```

Skip the call and the parser rejects `--value-flags` as unknown, the probe exits
non-zero, and `pr` falls back to its arity-blind scan — no error, just the occasional
flag value classified as the command's target. `claude-review` and `review-threads`
opt in this way; the rest inherit it from `ToolParser`.

One constraint comes with the protocol: every *option* the parser declares must
consume exactly one value. A flat list of option strings cannot express `nargs='?'`,
`'+'`, `'*'`, or an int above 1, so the probe refuses to answer rather than report a
wrong arity — it names the offending option on stderr, exits 2, and `pr` reprints the
message before degrading. Positionals are unconstrained (`claude-review` declares
`args` with `nargs='*'`).

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
