---
title: AI Automation
description: Claude Code integration for coding guidelines, intelligent skills, and AI-powered git automation.
---

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

### `/pr-rebase [branch] [--no-fix] [--no-push] [--force]`

AI-assisted rebase onto origin/main with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead).

```
/pr-rebase [branch] [--no-fix] [--no-push] [--force]
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

### Finding IDs and cross-references

Finding IDs (`M1`, `S2`, `N3`, `I1`) are assigned mechanically and are only
meaningful inside the review that carries them. Agents write whatever IDs they
like; merging, deduplication, and evidence verification all remove findings, and
a final pass closes the gaps so each severity numbers from 1 with no holes.

Only a *declaration* — a finding at the head of its own list item, `- **[M1]**
…` or `- [ ] **[M1]** …` — gets a number. Everything else that names an ID is a
reference, and references are rewritten through the same map, so a finding that
cites another one still cites the same one afterwards.

Brackets are what make a reference unambiguous. A bare `S3` is also an object
store and a bare `M1` is also a laptop, so an unbracketed mention only counts
when a citing phrase introduces it — `see S3`, `duplicate of S3`, `blocked on
S3`. Anything else is left as prose. The phrase list lives in
`_REFERENCE_CUES` in `ai/lib/review_findings.py`.

A reference to a finding that is no longer in the review becomes `[removed]`.
Leaving the ID alone would be worse than useless: the number it names has since
been reassigned to a different finding, and a reader who follows it lands
somewhere unrelated with nothing to signal the misdirection. Deduplication is
the exception — a duplicate is merged rather than dropped, so references to it
move to the copy that survived.

Text that declares no findings of a given severity is left untouched, since
there is no map to rewrite through and every ID in it belongs to some other
document. The same reasoning applies while groups are still being merged: each
group's IDs are shifted past the groups before it, references included, but a
reference the group cannot resolve is left alone — another group may well
declare it, and the merge-wide pass is the first place that can tell.

### When evidence verification drops a finding

Every must-fix and should-fix finding quotes the code it is about. After the
review is written, that quote is checked against the file: a finding whose
evidence does not match what is on disk is dropped, and the survivors are
renumbered. Roughly a quarter of reviews drop at least one finding this way.

The synthesis agent wrote the `## Summary` and the `## Verdict` before that check
ran, so both can describe findings that are no longer in the file. Regenerating
them would cost the agent's qualitative assessment, which is the part of a review
a reader cannot reconstruct from counts. So the prose stays and the review says
what left it:

- A blockquote at the end of `## Summary` names each dropped finding by severity
  and path — not by ID, since renumbering has already reassigned those — and why
  it was dropped.
- `## Verdict` is rewritten when the surviving counts no longer support the stated
  action. A drop can only remove findings, so this only ever lowers a verdict:
  `Request changes` → `Needs discussion` → `Approve`. A verdict the remaining
  findings still support is left exactly as written, and `Disapprove` is never
  touched — it means the overall approach is wrong, which the counts do not
  derive, so no drop refutes it.

Both are idempotent — a review that already carries the note is left alone, so
re-running post-processing does not stack notes or re-lower a verdict.

The lowering rule above only ever revises a verdict a drop leaves unsupported;
it is not the whole story of how a verdict ends up recorded. See the next
section for that.

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

### Where review artifacts live

Each review owns a directory under `~/.local/state/workbench/reviews/` — `review.md`
plus its session logs, group outputs, and pipeline state. The directory is derived
from the review file's path, and it is the only place outside the worktree that
review agents may write to. Granting the shared reviews root instead is how agent
scratch files ended up sitting beside unrelated reviews.

Each directory carries a `meta.json` sidecar, and that is what a review is
attributed by — the repo, the PR number, the head and base refs. The directory
name is for a human reading `ls`; nothing decides what a review is *for* by
parsing it, so a lookup is never answered by a similarly-named directory that
belongs to another repo. `meta.json` also carries two timestamps, which answer
different questions: `started_at` is stamped when a run begins, `reviewed_at`
only when it finishes with a review in hand. Neither is backfilled — a review
written before they existed dates from its `review.md` mtime and reports no
start.

Everything that reads the tree — the two `pr gc` sweeps and every review lookup —
walks it through one shared iterator (`review_common.iter_review_entries`), which
classifies each entry at the root as a review, an orphaned directory, or a stray
file. A new consumer reads that walk rather than adding a fourth set of rules for
what counts as a review.

`pr gc` collects loose files at the reviews root once they are a week old, prunes
review directories and run-target directories for merged and closed PRs (skipping
its own target), and sweeps the `state.json`, `run.lock`, and `trail.jsonl` the
pre-target layout left behind in a worktree's `.workbench/`. The directory itself
goes only when nothing else is in it. A flat `<name>.md` and its suffixed
siblings are left alone: those are input to the startup migration that folds the
old flat layout into directories.

The scheduled maintenance job (`otto-workbench maintenance start`) runs `pr gc`
each cycle, alongside its sync and stale-worktree cleanup — so this sweep, and
the terminal `pr_outcome` event it fires, no longer depends on someone typing
`pr gc` by hand. The step is skipped on an install without the ai component,
which is what puts `pr` on the path.

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

### What each `pr` command needs before it runs

Every subcommand declares what dispatch owes it, in its `_COMMANDS` entry in
[`ai/claude/bin/pr`](../ai/claude/bin/pr). Three independent axes, because they
routinely disagree:

| Axis | What it decides |
|---|---|
| **depth** | `local` resolves from git alone; `remote` adds the `gh` calls that name the repo and the PR |
| **fetch** | whether the worktree is fetched and fast-forwarded first |
| **lock** | whether the target's `run.lock` is held for the whole run |

| Command | Depth | Fetch | Lock |
|---|---|---|---|
| `create` | remote | no | yes |
| `status` | local | no | **no** |
| `ci` | remote | yes | yes |
| `review` | remote | yes | yes |
| `comments` | remote | yes | yes |
| `fix` | remote | yes | yes |
| `rebase` | remote | no | yes |
| `describe` | remote | yes | yes |
| `gc` | remote | no | yes |

`rebase` is the reason the axes are separate: it needs `gh` to name its PR and
does its own fetch, so a single "is this command remote?" flag would either
strand it or reset the worktree under it.

A command that declares nothing fails at import rather than silently picking up
a default — `_validate_needs` is the check, and it is what makes adding a
command a one-line edit in one place.

`status` is the only local one. It reads `state.json` and the worktree's push
state, and needs neither `gh repo view` nor `gh pr view` to do it: with no
`state.json` yet, the header names the repo from the origin-derived label
behind the repo key (`acme/widget`) rather than from `gh`. An explicit
`--pr <n>` escalates it to `remote` anyway — a PR number names a branch only
`gh` can report, and the branch is half the target key.

Every script's trail goes to one root — `~/.local/state/workbench/trail/YYYY-MM.jsonl`,
one file per month. `otto-log recent --repo <org/repo>` narrows it to one repo;
`otto-log query --pr <n>` finds every record for one PR, including the terminal
`pr_outcome` event `pr gc` writes when the PR merges or closes.

### Drafts, and what it takes to publish

`pr comments` writes nothing outward unless you pass `--post`. Replies, the fix
summary, thread resolutions, deferral tracking issues, and the push are all
printed to stderr as drafts instead, prefixed `DRAFT (not published)`. Code fixes
and the commit are unaffected: they are local and undoable, and they are what
makes the work reviewable at all. The gate covers what leaves the machine.

A hand-written `pr comments --reply <id> --body-file <path>` is no exception: it
drafts the body and reports the draft, and only `--post` sends it.

The default is draft because a review reply is public the moment it lands: an
incorrect claim has to be retracted in front of the reviewer, and a wrong
deferral issue has to be closed. Reading the drafts first costs one command:

```bash
pr comments --fix              # triage, fix, commit — drafts the push and replies
pr comments --finish --post    # publish once the drafts read correctly
```

A draft run leaves state untouched, so nothing is recorded as posted and a later
`--post` run picks up the same queue.

Filing the deferral tracking issue is the one thing `--post` may stop to ask
about. Nothing assumes a tracker: if `review.issue_tracker.provider` is unset for
the repo, a `--post` run asks where the repo files issues, then whether to record
the answer for this repo or for all of them. A repo-scoped answer is written to
`.workbench.yml` at the repo root — commit it and nobody is asked again. A
machine-wide answer goes to `config.yml` under the config root.

The question is only ever asked when it can be answered and the answer would
matter. A draft run does not ask, because it files nothing either way. A run with
no terminal — CI, a hook, a piped subprocess — reports the key to set instead of
asking. Either way an unanswered question files nothing: no tracking issue is
created and the deferral replies that would link to it are not sent, rather than
an issue being filed to a tracker nobody named.

### When a contested thread holds the gate shut

`--post` is a request, not a guarantee. If triage routes any thread to
`needs_human` — contested, conflicting, a question, or too complex to
auto-fix — the fix pass *holds* publishing for the rest of the process, and the
hold outranks `--post`. Nothing reopens it.

The fixes still get applied and still get committed. What waits is everything
that asserts the work is done: the push, the `Fixed in <sha>` replies, the thread
resolutions, and the summary. The commit sits locally with status `push_held`,
and `--finish --post` is what sends it:

```bash
pr comments --fix --post   # commits; holds the push, one thread is contested
# read the thread, answer the reviewer
pr comments --finish --post   # pushes, then drains the replies and the summary
```

Until that second command runs, the queue sits in state and the PR shows nothing
— an undelivered summary is indistinguishable from a run that had nothing to
say. `pr status` names it (`⚠ closeout owed: summary + 15 replies`) and counts
it as a merge blocker, so the hold survives the session that created it.

This exists because threads are triaged independently. A reviewer saying "the
root cause you describe does not exist" removes that one thread from the fixable
set and leaves the pass free to fix, push, and report success on everything else
— which is exactly [#703](https://github.com/otto-nation/otto-workbench/issues/703),
where 8 individually-real fixes were pushed to a branch that had already been
superseded.

The halt is deliberately blunt: any open thread, not just a premise-invalidating
one. Telling those apart is the hard classification problem, and the cost of
being wrong is asymmetric — a needless hold costs one extra command, while a
missed one costs a pushed commit and a reply claiming work is done. Running
`--fix` and `--finish` in the same invocation does not defeat it: the discussion
is still open at both points, so the hold applies to both.

### The supersession preflight

The same conclusion can be reached without any reviewer saying anything, by
three cheap checks in `supersession.py` that every branch-acting command runs
before it acts:

| Signal | What it reads | Evidence? |
|---|---|---|
| `rebase_skew` | author vs committer date on the branch's first commit, ≥ 7 days apart | no |
| `readds_removed_symbol` | a definition in `git diff origin/<default>...HEAD` that the default branch no longer contains but once did | yes |
| `superseding_pr` | a merged PR mentioning that symbol, via `gh api search/issues` | yes |

Each finding is printed with its kind, so the output says which check fired.
Only the last two count as evidence: a branch replayed onto a base that has
moved is what makes supersession visible, but on its own it describes every
long-lived branch, and acting on it would fire on the healthy case.

It is a preflight, not an investigation — the symbol scan stops at the first ten
definitions and only the first two flagged symbols are searched for on GitHub, so
a clean branch costs two local git commands and no network call at all. The
verdict is cached in the state file against the HEAD *and* base SHAs it was
computed from, so the next command on the same branch reuses it rather than
repeating the search; a moved base invalidates it just as a moved HEAD does,
because there is nothing to re-add until the default branch deletes it — a
branch whose own HEAD never moves becomes superseded the moment `main` does.

**One detection, two policies.** What a positive verdict does depends on where
the money is:

| Command | Response | Why |
|---|---|---|
| `pr comments --fix` | holds publishing | The triage pass is already paid for by the time this could stop it. Stopping saves nothing; what must not happen is asserting outward that superseded code was fixed. |
| `pr review` / `pr review --self` | refuses, exit 4 | A review is the largest model spend in the repo and the check runs before the first agent call, so refusing costs nothing and saves all of it. |

The hold in `pr comments` reaches the same acts a contested thread's hold does —
the push, the replies, the resolutions, and the summary, but not the local
commit.

The refusal in `pr review` prints the signals and writes the same JSON shape
`pr rebase` uses for its already-landed refusal, on the same exit code:

```json
{
  "branch": "isaac/703/fix_the_thing",
  "status": "superseded",
  "signals": [
    {
      "kind": "readds_removed_symbol",
      "detail": "`dropped_helper` is added by this branch but absent from origin/main, which last touched it in abc1234 (ai/lib/foo.py)",
      "holds": true
    }
  ],
  "override": "--force"
}
```

Read the merged PR the `superseding_pr` signal names before doing anything else.
If the branch really is still wanted, re-run with `--force`, which skips the
check entirely. `pr fix` stops on the refusal rather than continuing to its CI
pass: every remaining pass acts on the same branch, so one refusal answers for
all of them.

Two flags do *not* override it, and one does. `--post` and `--no-post` set the
same internal flag `--force` does — they suppress the confirmation prompts,
because nobody is present to answer one — but an unattended run is the one this
refusal most has to survive, so the check reads the raw `--force` instead.
`--recover` is exempt on both entry points: it finishes a run whose spend was
already made, so refusing it saves nothing and strands the artifacts of the run
it was asked to complete.

This is distinct from `pr rebase`'s already-landed check, which asks whether the
work has *landed* rather than whether it has been *superseded* — work can land
without the branch being superseded, and a branch can be superseded without its
commits having landed anywhere, because someone solved the problem differently.
They share the exit code and the override flag, and nothing else.

### The summary comment is the record, not the state file

The `Review Comments Addressed` comment is what a reviewer reads to confirm
their feedback was accounted for, and it is the one place a whole cycle is
tallied. A cycle keeps editing one comment rather than appending a summary per
round, so every round has to leave it at least as complete as it found it.

Three things follow, and the first two were bugs —
[#714](https://github.com/otto-nation/otto-workbench/issues/714) and
[#712](https://github.com/otto-nation/otto-workbench/issues/712):

**Every outcome is reported, including the ones nobody resolved.** A
`needs_human` thread is the case that took the most operator judgment, so
omitting it is the worst row to lose. It renders as open, with its reason. If
the operator settled it outside the tool — answered the thread, fixed it by
hand — `--finish` reconciles the snapshot against GitHub first and the row
credits that work instead of reporting a discussion that already happened.

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
another machine. Building the replacement body purely from state then deletes
rounds nobody can recover. So the published body is read first, matched row by
row on the thread permalink, and anything unaccounted for is re-emitted
verbatim, counted as `N carried over`, and warned about on the run.

The table therefore only grows. A row no later round reproduces is carried for
the life of the PR, so a thread that legitimately stops appearing keeps its last
rendered state rather than vanishing. That is the trade being made on purpose: a
stale row a reviewer can still read beats a round nobody can recover.

**A summary that has been answered is reposted, not edited.** GitHub leaves an
edited comment where it was and notifies nobody, so once a reviewer has
commented, submitted a review, or replied on a thread below the summary, editing
it writes the round's outcome somewhere the reader has already scrolled past.
Each round compares the summary's `created_at` against the newest activity that
is not ours — our own thread replies do not count, or the fix pass would trip
the check on itself every round — and posts a fresh comment when it lost the
last word. The fresh body carries the marker and every row the published one
held, so the newest summary is always the complete one and the next round finds
it; the superseded comment is left untouched as that round's record.

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
server discovers tools — it probes every executable in the workbench's component `bin/`
directories, plus any `tool_dirs` adds (see [`tools.md`](tools.md#otto-mcp-server)) — and
it is what the skill reference above cites for `ci-check` and `pr-rebase`.

Naming the flag in a script under one of those directories is therefore a claim, and
`bin/local/validate-tool-schema` holds the build to it: it probes every candidate
discovery would and fails when one cannot answer. `bin/local/validate-skills` asserts the
converse for the tool a skill's `output_schema` names — that one must implement the
protocol whether or not it carries a marker, or the skill cites a contract nothing
publishes.

The output schema is generated from the tool's dataclass by
[`ai/lib/schema_gen.py`](../ai/lib/schema_gen.py), which describes what
[`serde`](../ai/lib/serde.py) will accept for each field rather than deciding that
for itself: `serde.classify` sorts a type hint into a `HintKind`, and both the
reader and the schema emitter dispatch on that one answer. A new kind fails a test
in every module that has to handle it, which is what keeps the published contract
from drifting from the reader.

One case needs the dataclass's help. A class that reads more than one stored shape
through `_from_raw` — a legacy string, a renamed key — is the only thing that knows
what those shapes are, so it also defines `_raw_schema(object_schema)`, returning
the widened fragment. Without it the published schema would call a document invalid
that `serde` reads without complaint; a test fails any `_from_raw` class in
`ai/lib/` that does not define one.

`--value-flags` prints one option string per line: every option of that parser that
consumes a following value. `pr` asks a delegate this before deciding whether a bare
token is the command's target or some other flag's argument. Without it,
`pr comments --reply 3777767789` reads the reply ID as a PR number and swallows it.

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

### What a failed command is allowed to say

A wrapper that returns `(returncode, stdout)` has no slot for stderr, and stderr is
where a command explains itself. Everything downstream inherits that gap: the
renderer has no cause to print, and a classifier reading stdout alone misses a
failure the command reported on the other stream. `gh api` is the sharp case — it
writes an API error body to stdout and its own status line (`gh: ... (HTTP 503)`) to
stderr, so a 404 is legible from stdout while a 5xx or a dropped connection leaves
stdout empty.

[`ai/lib/proc.py`](../ai/lib/proc.py) is the answer: `proc.run(cmd)` returns a frozen
`CmdResult` carrying `returncode`, `stdout`, and `stderr`, and a caller reads what it
needs by name rather than by position.

| Read | What it gives you |
|---|---|
| `r.ok` | The command exited cleanly. |
| `r.detail` | `stderr` folded onto one line — what to quote in an error. |
| `r.combined_output` | Both streams, for classifying a failure by what it said. |
| `r.server_error` | The failure was a 5xx, so the remedy is to wait and retry. |

`proc.failure_message(action, r)` renders a failure without asserting a cause the
code has not established: it names the action, appends whatever the command said,
and calls out a 5xx separately because that is the one case where the answer is to
wait rather than to change anything. It decides that from `server_error`, so the
rendered message and the classifier can never disagree about which stream the
evidence was on. It accepts a raw `subprocess.CompletedProcess` too, so the call
sites still running `subprocess.run` directly can report a failure without
converting first.

`proc` is stdlib-only on purpose — it is the module the rest of `ai/lib` should be
free to depend on. The name is not `cmd` because `ai/lib` goes on `sys.path` ahead
of the standard library, where a `cmd` module would shadow the one `pdb` imports.

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
