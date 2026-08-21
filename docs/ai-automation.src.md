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

Both components are readable from a checkout with no network call, but that is a
convenience for this repo's own code, not an invitation to rebuild the path
elsewhere: `ai/lib/pr_target.py` is the owner, and another repo that wants to
know what has been reviewed asks the CLI (§ Publishing review state) rather than
deriving where a review would sit.

### Publishing review state

Another repo learns what this machine has reviewed by asking:

```
pr review --list --schema-version 1
```

The caller declares the row schema it speaks and the CLI enforces it, exiting
non-zero and naming the versions it serves if the value is unsupported. A bare
`pr review --list` writes a human table to stderr and nothing at all to stdout —
a consumer that forgets the handshake gets a `jq` parse failure rather than a
subtly-wrong document.

`stdout` carries one JSON object:

```json
{
  "schema_version": 1,
  "reviews": [
    {
      "repo": "otto-nation/otto-workbench",
      "pr_number": 761,
      "review_file": "/Users/…/reviews/otto-workbench-761/review.md",
      "head_sha": "4a33027c…",
      "head_ref": "isaac/761/…",
      "base_ref": "main",
      "review_type": "full",
      "mode": "pr",
      "reviewed_at": "2026-08-18T14:02:11+00:00",
      "started_at": "2026-08-18T13:47:03+00:00",
      "findings": {"must_fix": 0, "should_fix": 2, "nit": 1, "idiom": 0, "total": 3},
      "verdict": "approve",
      "status": "complete",
      "failure_detail": "",
      "cost_usd": 4.12,
      "input_tokens": 0, "output_tokens": 0,
      "cache_read_tokens": 0, "cache_write_tokens": 0,
      "duration_ms": 0
    }
  ]
}
```

A row reports its review's *path*, never its content: a consumer polling on an
interval would otherwise carry every review's full text on every tick. Finding
keys are the `SeverityConfig.json_key` vocabulary the rest of the codebase
already uses, so this document and `build_review_summary`'s cannot disagree
about what a severity is called. A review written before `meta.json` existed is
still listed, with an empty repo and a null PR number — unattributed is a fact
about that review, and dropping it would hide one the consumer can still open.

A missing reviews root is not an error; it is `{"reviews": []}` with exit 0. The
*process* is the error channel: a `pr` that is absent, crashes, or exits
non-zero is what a consumer must treat as "unknown". That is the whole reason a
query beats deriving the path — a root nothing writes to reads exactly like a
machine that has never run a review.

**Version policy.** A new *optional* field does not bump `schema_version`. A
removed field, a renamed field, or a changed type adds a new version.
Enforcement comes from the supported set being allowed to *shrink* —
`--schema-version 1` keeps working until this build stops serving 1, and
`ai/lib/review_listing.py`'s `SCHEMA_VERSIONS` is the one place that says which
those are. Nothing hand-stamps a version into the document: the field echoes
back what the caller declared and this build agreed to serve, so it cannot go
stale on its own.

### What each `pr` command needs before it runs

Every subcommand declares what dispatch owes it, in its `_COMMANDS` entry in
[`ai/claude/bin/pr`](../ai/claude/bin/pr). Three independent axes, because they
routinely disagree:

| Axis | What it decides |
|---|---|
| **depth** | `none` resolves nothing at all; `local` resolves from git alone; `remote` adds the `gh` calls that name the repo and the PR |
| **fetch** | whether the worktree is fetched and fast-forwarded first |
| **lock** | whether the target's `run.lock` is held for the whole run |

| Command | Depth | Fetch | Lock |
|---|---|---|---|
| `create` | remote | no | yes |
| `status` | local | no | **no** |
| `ci` | remote | yes | yes |
| `review` | remote | yes | yes |
| `review --summary` / `--post` / `--repair` / `--recover` | remote | **no** | yes |
| `review --list` | **none** | no | **no** |
| `comments` | remote | yes | yes |
| `fix` | remote | yes | yes |
| `rebase` | remote | no | yes |
| `describe` | remote | yes | yes |
| `gc` | remote | no | yes |

`review` is the one command whose need its arguments decide, which is why its
`_COMMANDS` entry holds a resolver rather than a `Need`. The fetch is the line
between its two halves: a bare `pr review` is about to review the branch, so it
wants the branch current, while every mode flag acts on a review that already
exists at the commit that review describes. Fast-forwarding under one of those
would leave `--summary` and `--post` reporting a review of a commit the
worktree no longer sits on, and would push `--recover` off the SHA it then has
to pin a throwaway worktree back to.

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

`review --list` is the only `none` one, and `none` is not "resolve less" — it is
"there is nothing to resolve". The listing answers from the user's own state
root, so it has no repo, no branch, and no target, and unlike `local` it works
from a directory that is not a git repository at all. `--pr` does not escalate
it: there is no target for a PR number to name at that depth, so honouring one
would spend a `gh` call on a value the handler never reads.

`review --list` is also the one invocation that writes no trail at all. Resolving
nothing and holding no lock is the shape of a query rather than of an action, and
the listing exists to be polled: the two records a dispatch writes cost more than
the query itself, and they land in the file every `otto-log` query then reads. The
exemption is read off the same three axes as everything else — `Need` carries no
trail flag of its own for a command to add itself to.

Every script's trail goes to one root — `~/.local/state/workbench/trail/YYYY-MM.jsonl`,
one file per month. `otto-log recent --repo <org/repo>` narrows it to one repo;
`otto-log query --pr <n>` finds every record for one PR, including the terminal
`pr_outcome` event `pr gc` writes when the PR merges or closes.

The root keeps six months, counting the month in progress (`TRAIL_KEEP_MONTHS` in
[`ai/lib/trail.py`](../ai/lib/trail.py)). Every trail drops what falls outside the
horizon as it opens, so growth is bounded whatever writes to the root, and
`otto-log prune --keep <n>` sweeps at a horizon you name when a machine is short
of space. A file whose stem is not a month — `legacy.jsonl`, where the cutover
migration parked the pre-cutover history — is never dropped: its name cannot
place it in time, and nothing appends to it, so it is a fixed size rather than a
source of growth.

### Drafts, and what it takes to publish

`pr comments` writes nothing outward unless you pass `--post`. Replies, the fix
summary, thread resolutions, deferral tracking issues, the PR description, and
the push are all printed to stderr as drafts instead, prefixed
`DRAFT (not published)`. Code fixes and the commit are unaffected: they are local
and undoable, and they are what makes the work reviewable at all. The gate covers
what leaves the machine.

A hand-written `pr comments --reply <id> --body-file <path>` is no exception: it
drafts the body and reports the draft, and only `--post` sends it.

Some comments are answered by rewriting the PR description rather than the code.
That is a GitHub write like any other, so the fix agent does not make it: it is
barred from running `gh` at all, and instead writes the replacement description
to `ignore/pr-comments/pr-description.md` in the worktree. The fix pass sends it
through the same gated client the replies use, which means a run without `--post`
records the intended edit and performs none. The undelivered description is owed
in `pr status` alongside the replies (`⚠ closeout owed: PR description`) and
`--finish --post` delivers it.

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
about. Nothing assumes a tracker: if `issue_tracker.provider` is unset for
the repo, a `--post` run asks where the repo files issues, then whether to record
the answer for this repo or for all of them. A repo-scoped answer is written to
`.workbench.yml` at the repo root — commit it and nobody is asked again. A
machine-wide answer goes to `config.yml` under the config root.

The question is only ever asked when it can be answered and the answer would
matter. A draft run does not ask, because it files nothing either way. A run with
no terminal at all — CI, or anything else detached from one — reports the key to
set instead of asking. A piped stdin is not that: the question goes to the
terminal the command was started from, so a `--post` run piped into `tee` still
asks. Either way an unanswered question files nothing: no tracking issue is
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

### What `pr rebase` refuses to rebase

The already-landed signals answer "is this work already in the base?". Two more
answer a different question — "is replaying this branch onto that base a safe
thing to do at all?" — and refuse on the same exit code, with the same `--force`
override:

| Signal | What it reads | When it fires |
|---|---|---|
| `no_merge_base` | `git merge-base <base> HEAD` exits nonzero | The branch and its base share no commit |
| `conflicts_over_budget` | distinct conflicted files across the whole rebase | The count passes `_CONFLICT_FILE_BUDGET` |

`no_merge_base` is exact rather than heuristic, and it costs one local git
command, so it is asked before the landed signals rather than after them — those
compare HEAD against a ref an unrelated branch has no relationship to, so they
answer nothing there. A repo that was re-initialised leaves branches descending
from a second root; rebasing one replays its entire history onto a base it has
nothing in common with, which conflicts in every file both roots happen to
contain.

A ref that does not resolve is not this. `git merge-base` fails identically for a
typo'd `--onto` and for a base branch the fetch never brought down, so the check
verifies the ref names a commit first and passes when it does not — refusing
those as unrelated history would send the operator after a root they do not
have, where git's own error for the missing ref says what actually went wrong.

The budget is the circuit breaker for what that produces. Conflict resolution is
an AI call per conflicted file, with edit access to the worktree, and the wider
the spread the less any single call can tell an intended change from an
unrelated one — which is how a rebase resolving 51 conflicts rewrote
`bin/otto-workbench`, a file the branch never touched, into invalid bash. Past
the budget the rebase is aborted before the first resolution call, so the
worktree is left clean rather than half-replayed.

The count is of *distinct files* across the whole rebase, not conflicts: a file
conflicting in every replayed commit is one file's worth of risk, and counting
it once per commit would refuse a narrow rebase over a long branch. The tally
carries across steps, so a rebase that widens gradually is refused at the step
that crosses the line rather than never.

A resumed rebase waives the budget. The conflicts are already sitting in the
worktree by then; refusing would strand it mid-rebase with no path forward
except the manual resolution the command exists to avoid. The waiver is the
resume path passing `force=True` into the same parameter `--force` sets, so
there is one waiver mechanism rather than two.

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

The AI backend owes the same thing. `claude -p` reports some failures on stdout
with an empty stderr — a usage limit is the common one — so a stderr-only error
message prints nothing at all and the caller is left reporting a bare exit code
with no reason attached. `ai_backend_claude` logs whichever stream carried the
detail, and the exit code alone when neither did.

An expired timeout is the same kind of answer. `proc.run` converts it into a
`CmdResult` carrying `proc.TIMEOUT_RETURNCODE` (124, the shell convention) with the
bound and the command quoted on stderr, and whatever the process managed to write
before it was killed preserved on both streams. Raising instead would need a handler
at each of the call sites that has none; as a result code it degrades through
`out`/`ok`/`lines` exactly as any other failure does. The code is contract rather
than an implementation detail — the eval scorers tell a timed-out case from a failed
one by it.

`proc` is stdlib-only on purpose — it is the module the rest of `ai/lib` should be
free to depend on. The name is not `cmd` because `ai/lib` goes on `sys.path` ahead
of the standard library, where a `cmd` module would shadow the one `pdb` imports.

### One table of timeouts

`ai/` decided how long a subprocess may run in 22 places and four different ways:
module-scoped constants, bare literals, a caller-supplied argument, and — for most of
the git client — nothing at all. The same operation got a different bound depending
on which file called it; one `gh api` round trip was 30s in `review_github`, 10s in
`pr-rebase`, and 10s in `retro-scan`. No principle separated those numbers.

Leaving the choice with callers is what produced the spread, so
[`ai/lib/timeouts.py`](../ai/lib/timeouts.py) takes it away from them. A caller picks
a tier that describes its operation; it does not pick a number. The axis is not how
long the work takes but **what bounds its cost**.

| Tier | For | Why |
|---|---|---|
| `QUICK` | A `--value-flags` probe, a session hook reading one file | Should answer instantly; a breach is a wedged process, never real work. |
| `LOCAL` | Flat-cost local reads — `rev-parse`, `merge-base`, `log`, `grep`, `diff`, a `yq` parse | Scales with neither history nor tree size in any way that approaches the bound. |
| `NETWORK` | One round trip — a single `gh api` call, a tracker CLI, an HTTP request | Bounded by latency, not payload, so a breach means the far end stopped answering. |
| `TRANSFER` | Data-proportional over a socket — `fetch`, `gh api --paginate` | As large as the history or the result set, but a socket can stall in a way waiting will not fix. |
| `UNBOUNDED` | `worktree add`, `commit`, `push` | A bound would be wrong, not merely large — see below. |

Operation-bounded work costs the same whatever the repository holds, so exceeding the
bound means something is genuinely wrong and a tight bound is a hang detector.
Data-bounded and user-bounded work costs whatever the input costs: `git worktree add`
materializes every file in the tree, and `commit`/`push` run hooks belonging to
whatever repository is being operated on — routinely a secret scan, a linter, or a
full test suite. A fixed bound there silently converts a large repo, or a thorough
hook, into a broken tool.

`UNBOUNDED` is a named `None` rather than an omitted argument so that running
unbounded stays a decision on the record: `proc.run` requires `timeout=`, and
`bin/local/validate-timeouts` rejects both a numeric literal and a bare `None` on any
`timeout=` argument under `ai/`. It also rejects a `proc.run` or `subprocess.run` call
that writes no `timeout=` at all — reading only the bounds that were written down left
the omission invisible, which is the case the table exists to eliminate, since a call
with no bound is indistinguishable from nobody having thought about one.
`ai/claude/mcps/server.py` is exempt — it runs under `uv run` with `ai/lib` nowhere on
`sys.path`, so it cannot import the table.

Two numbers deliberately stay outside it. `ci-check --wait-timeout` and
`eval_task.EVAL_CASE_BUDGET` are deadlines for work that could reasonably keep going,
not bounds on a subprocess that should already have answered; they say how long
something is *worth*, which is a different question.

### One way to run git

`ai/` invoked `git` as a literal argv head in 131 places, and each one re-decided the
same four things: `-C` or `cwd=`, whether to capture, whether a non-zero exit is a
failure or an answer, and what to do with stderr. That spread is why a fix applied to
one call site was never a fix for the other hundred and thirty.

[`ai/lib/git_client.py`](../ai/lib/git_client.py) sits directly on `proc` and depends
on nothing else. The runner is `run`; the other three are the shapes callers actually
wanted from it.

| Call | What it gives you |
|---|---|
| `run(*args, cwd=, config=)` | The full `CmdResult`. Never raises on a non-zero exit — `diff --quiet`, `cat-file -e` and `rev-parse --verify` all answer a question with theirs. |
| `out(*args, default="")` | Stripped stdout, or `default` when git exited non-zero. |
| `ok(*args)` | Whether git exited cleanly, for the subcommands that answer a question that way. |
| `lines(*args)` | Stdout split into non-empty lines. |

There is no `timeout` parameter. The bound follows from the subcommand the same way
`core.quotePath` does — `fetch` takes `TRANSFER`, `worktree`/`commit`/`push` run
`UNBOUNDED`, and everything else is a flat-cost metadata read at `LOCAL` — so the
knowledge lives with the client that owns it rather than at each of the forty-five
call sites, one of which used to pass a number of its own.

`config={"key": "value"}` becomes `-c key=value` ahead of the subcommand. `diff`,
`ls-files` and `status` get `core.quotePath=false` by default: git escapes a
non-ASCII path in that output unless told otherwise, and an escaped name is not a
pathspec a later `git add` can resolve — so a fix touching such a file was staged as
nothing and reported as applied. Applying the flag to the subcommand rather than to
each caller is what stops the next call site from forgetting it.

Under those four sit the reads that appeared at two or more call sites — `head_sha`,
`current_branch`, `is_dirty`, `commit_exists`. A read used once belongs at its call
site, spelled out with `run`. Writes are not modelled beyond `run`: committing and
pushing gets an owner of its own, with the publishing gate over it, rather than a
convenience wrapper here that would turn four gate-less push sites into five.

Not everything has moved across yet. `pr_context`, `pr-rebase`, `mcps/server` and
`eval_task` still invoke git as literal argv — the first three because a behaviour fix
is open on them and a refactor underneath it would collide, `eval_task` because its
calls need `env=`, which the client does not take — it runs them through `proc.run`,
which does. A new call site should still go through the client.

One consequence of the move is worth knowing before migrating the rest: the client
passes the worktree as `cwd` rather than as `git -C`. A root that does not exist used
to come back as a non-zero exit that `out` and `ok` degraded away; it now raises
`FileNotFoundError` out of Python before git is reached. That is the better answer —
an absent worktree is a broken caller, not a question git declined to answer — but a
call site that was quietly relying on the old degradation will start failing loudly.

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
