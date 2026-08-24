---
title: AI Libraries
description: Every module in ai/lib/ — the Python behind pr review, pr comments, pr ci, and the eval harness.
---
<!-- Generated from docs/ai-libraries.src.md by bin/local/compose-docs — do not edit. -->

# AI Libraries

Every module in `ai/lib/`, grouped by what it is for. This is the reference; [AI Automation](ai-automation.md) is the guide, and holds the setup, the configuration, and the flow that crosses several of these modules at once.

Each section below is the module's own docstring, rendered from `ai/lib/` by [`generate-doc-reference`](../bin/local/generate-doc-reference) — so the prose describing a module lives beside the code it describes, and a module that moves takes its documentation with it. A module declares which group it belongs to with a `# doc-group: <key>` comment under its docstring; nothing here lists module names, so adding a module changes only the module.

## Running a review

The orchestration of a review run: what it checks before spending anything, how the work is split into phases, what each agent is asked, and where the run's artifacts live.

### agent_retry.py

Shared guard against agents that finish without producing anything.

An agent that runs to its own conclusion having never called a write tool was
thrashing, not working.  The review pipeline learned to diagnose that and give
it one more attempt with a hint naming the write mechanism; every `pr` script
that drives an agent needs the same guard, so it lives here rather than inside
review_retry.

Two shapes are supported, matching the two ways the `pr` scripts call an agent:

  retry_unproductive  — an agent with tools whose work lands in a file or a
                        tracking checklist.  Diagnosed from its session log.
  retry_blank_response — a stateless prompt whose answer must parse.  There is
                        no session log, so the response itself is the signal.

### prompt.py

Terminal questions, for the few commands that have one to ask.

Separate from ``log`` because that module owns output and this one owns
input. Both readers fall back to ``/dev/tty``: these run inside commands
whose stdin is often a pipe, and a piped stdin does not mean there is
nobody at the keyboard.

Every function has a no-answer value — ``False`` and ``""`` — so a caller
in a hook, a CI job, or a subprocess gets a usable result instead of an
exception. No answer is never consent.

### review_agent.py

Agent invocation, cost tracking, model selection, and diagnostics.

Delegates actual AI invocation to ai_backend (which dispatches to
Claude Code CLI or Pi CLI based on AI_BACKEND env var). This module
adds cost tracking, failure diagnosis, and output recovery on top.

**Which model a phase uses.** Every review phase resolves its model through one
chain, most specific first:

1. an explicit ``--model`` on the command
2. the phase's own key — ``CLAUDE_REVIEW_GROUP_MODEL``,
   ``CLAUDE_REVIEW_HOLISTIC_MODEL``, ``CLAUDE_REVIEW_SINGLE_MODEL``,
   ``CLAUDE_REVIEW_SCOUT_MODEL``, ``CLAUDE_REVIEW_DISPROVE_MODEL``,
   ``CLAUDE_REVIEW_FIX_MODEL``, ``CLAUDE_REVIEW_SYNTHESIS_MODEL``
3. ``CLAUDE_REVIEW_MODEL``, which covers every phase at once
4. the phase's built-in default

Whichever wins, a bare tier alias (``sonnet``, ``opus``, ``haiku``) is then
resolved through ``ANTHROPIC_DEFAULT_SONNET_MODEL`` /
``ANTHROPIC_DEFAULT_OPUS_MODEL`` / ``ANTHROPIC_DEFAULT_HAIKU_MODEL``. An alias
names a tier, not a deployment — on Vertex and Bedrock the account provisions a
specific model ID, and that is where it lives. A concrete model ID anywhere in
the chain passes through untouched.

The Claude CLI does this resolution itself; the Pi backend does not, so the
resolution happens here before dispatch and both backends land on the same
model.

### review_gc.py

Removal of review artifacts, at every lifecycle that removes one.

Three sweeps, one module, so that nothing else in the review system deletes a
review's files: the sweep at the end of a successful run (`cleaned_on_success`),
the stale-intermediate sweep and orphan collection `pr gc` runs, and the prune
of reviews whose PR has been merged or closed.

They differ only in what makes a file collectable — the run being over, age, or
the PR being gone — and all of them read what a review directory holds from
`review_common.phase_artifacts` rather than naming files themselves.

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

### review_phases.py

Phase registry and executors for the review pipeline.

A review is a sequence of agent phases, and this module owns what a phase *is*:
the built-in spec (`PhaseSpec`, `PHASES`), the resolution of a spec plus an
effort preset into the seven values an invocation needs (`PhaseRunner`), the
turn budgets, and the executors that actually run each phase.

The group fan-out lives here too — serial, parallel, retry and the
previously-skipped sweep are all ways of running the group phase, and they
share the executor and its budget rules.

What a phase *produces* is somebody else's problem: the review document, the
synthesis and the run drivers stay in review_pipeline.

### review_pipeline.py

Pipeline orchestration for claude-review.

Drives the single-agent and multi-phase runs end to end: sequencing the phases
review_phases defines, deciding what a resumed run may skip, assembling the
review document (synthesis, mechanical fallback, meta header), consolidating
the session logs, and fetching the PR metadata a run starts from.

The run ends when the review file is written — what happens to the findings
afterwards belongs to review_fix, and removing what the run left behind belongs
to review_gc, which the orchestrator runs once every phase is done.

### review_preflight.py

Pre-flight data collection, tier classification, file grouping, and PR fetching.

Handles everything needed before prompt construction: collecting diffs, commit logs,
file contents, permissions, and organizing files into review groups.

### review_profiles.py

Review profiles: per-domain review doctrine routed by file paths.

Profiles live in `.claude/review/profiles/*.yml` and contain structured
review rules matched against changed file paths. Group reviewers receive
only the profiles relevant to their files.

### review_prompt.py

Prompt construction and template rendering for claude-review.

Handles building prompts for each review template: single, holistic, group,
synthesis, self-review, and self-review-synthesis. Includes section builders,
budget computation, and prompt size logging.

### review_retry.py

Retry and diagnosis routing for the review pipeline.

Everything here decides *what to do* about an agent that produced nothing —
whether the failure is worth another attempt, how many turns that attempt gets,
how the reason renders, and when a run of failures is systemic enough to stop
the pipeline. None of it runs a phase, so it stays callable from the phase
executors and the orchestration layer alike.

The hints, the retryability test and the retry driver are shared with the other
`pr` scripts — see agent_retry. Aliased here so the review modules keep reading
the way they always have.

### review_scout.py

Lead scout: parse structured investigation leads from scout phase output.

The scout phase replaces the holistic phase's prose output with structured
leads that tell group reviewers exactly where to focus investigation.

### review_sections.py

Config-driven section registry with auto-discovery for review posting.

Defines section configs declaratively and extracts them from review markdown.
Replaces per-section parameter threading across the posting pipeline.

### review_state.py

Pipeline run state for the review pipeline.

The multi-phase pipeline writes a `pipeline-state.json` sidecar as it goes so a
crashed run can be resumed rather than repeated. Everything that reads, writes,
validates or renders that state lives here: the persistence itself, the resume
decision (`_resolve_recovery`), and the Agent Failures table the state feeds
into the review document.

Kept apart from the phases so the state is describable without running one —
the recovery path, the tests and the phase executors all reach the same
functions.

### review_static_analysis.py

Static analysis framework for the review pipeline.

Runs machine-checkable tools against changed files and formats violations
for inclusion in review output. Each checker is a plain function with the
signature: (changed_files: list[str], wt_path: str) -> CheckerResult | None.

### review_worktree.py

Worktree lifecycle management for claude-review.

## Findings

What a review produces. Parsing an agent's output into findings, giving them stable IDs, merging duplicates, disproving the ones that do not hold up, and rendering what survives.

### review_common.py

Shared constants, types, and helpers for the claude-review system.

This module is the contract between review-orchestrate and review-post.
Both scripts import from here instead of defining their own constants.

Each review owns a directory under `~/.local/state/workbench/reviews/` —
`review.md` plus its session logs, group outputs, and pipeline state. The
directory is derived from the review file's path, and it is the only place
outside the worktree that review agents may write to. Granting the shared
reviews root instead is how agent scratch files ended up sitting beside
unrelated reviews.

Each directory carries a `meta.json` sidecar, and that is what a review is
attributed by — the repo, the PR number, the head and base refs. The directory
name is for a human reading `ls`; nothing decides what a review is *for* by
parsing it, so a lookup is never answered by a similarly-named directory that
belongs to another repo. `meta.json` also carries two timestamps, which answer
different questions: `started_at` is stamped when a run begins, `reviewed_at`
only when it finishes with a review in hand. Neither is backfilled — a review
written before they existed dates from its `review.md` mtime and reports no
start.

Everything that reads the tree — the two `pr gc` sweeps and every review lookup
— walks it through one shared iterator (`iter_review_entries`), which classifies
each entry at the root as a review, an orphaned directory, or a stray file. A
new consumer reads that walk rather than adding a fourth set of rules for what
counts as a review.

### review_dedup.py

Deduplication of findings against already-posted PR comments.

Fetches existing bot comments (inline and review-body), compares via
Jaccard similarity, and filters out duplicates before posting.

### review_disprove.py

Disprove-it gate: adversarial falsification of review findings.

After synthesis, each Must-fix and Should-fix finding is challenged.
Findings that cannot survive scrutiny are dropped before posting.

Every must-fix and should-fix finding quotes the code it is about. After the
review is written, that quote is checked against the file: a finding whose
evidence does not match what is on disk is dropped, and the survivors are
renumbered. Roughly a quarter of reviews drop at least one finding this way.

The synthesis agent wrote the ``## Summary`` and the ``## Verdict`` before that
check ran, so both can describe findings that are no longer in the file.
Regenerating them would cost the agent's qualitative assessment, which is the
part of a review a reader cannot reconstruct from counts. So the prose stays and
the review says what left it:

* A blockquote at the end of ``## Summary`` names each dropped finding by
  severity and path — not by ID, since renumbering has already reassigned those
  — and why it was dropped.
* ``## Verdict`` is rewritten when the surviving counts no longer support the
  stated action. A drop can only remove findings, so this only ever lowers a
  verdict: ``Request changes`` → ``Needs discussion`` → ``Approve``. A verdict
  the remaining findings still support is left exactly as written, and
  ``Disapprove`` is never touched — it means the overall approach is wrong,
  which the counts do not derive, so no drop refutes it.

Both are idempotent — a review that already carries the note is left alone, so
re-running post-processing does not stack notes or re-lower a verdict.

This lowering rule only ever revises a verdict a drop leaves unsupported. How a
verdict is decided in the first place belongs to ``ReviewVerdict``.

### review_findings.py

Finding parsing, renumbering, deduplication, verification, and stable IDs.

Shared between review-orchestrate (merging/verification) and review-post (parsing).

Finding IDs (``M1``, ``S2``, ``N3``, ``I1``) are assigned mechanically and are
only meaningful inside the review that carries them. Agents write whatever IDs
they like; merging, deduplication, and evidence verification all remove
findings, and a final pass closes the gaps so each severity numbers from 1 with
no holes.

Only a *declaration* — a finding at the head of its own list item, ``- **[M1]**
…`` or ``- [ ] **[M1]** …`` — gets a number. Everything else that names an ID is
a reference, and references are rewritten through the same map, so a finding
that cites another one still cites the same one afterwards.

Brackets are what make a reference unambiguous. A bare ``S3`` is also an object
store and a bare ``M1`` is also a laptop, so an unbracketed mention only counts
when a citing phrase introduces it — ``see S3``, ``duplicate of S3``, ``blocked
on S3``. Anything else is left as prose; ``_REFERENCE_CUES`` is the phrase list.

A reference to a finding that is no longer in the review becomes ``[removed]``.
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

### review_fix.py

Fix pass for claude-review.

Runs after a review is written and `--fix` is set: hands the review document to
an agent that applies what it can, reconciles the checkboxes against the files
the agent changed, then commits and pushes the result.

What the agent changed is a snapshot difference: the worktree's dirty set is
recorded before the agent runs and again after, and only the paths that appear
in the second and not the first are attributed to it. Without that first
snapshot the pass cannot tell its own work from whatever was already sitting in
the worktree, and it both commits and takes credit for the difference.

It sits downstream of the pipeline rather than inside it — nothing here runs
during a review, and a fix pass needs only a finished review file to work from.

### review_format.py

Diff classification, renumbering for posting, comment formatting, and permalinks.

Classifies findings as inline/file-level/skipped based on the PR diff,
renumbers them for posted order, and formats as GitHub review comments.

### review_prior.py

What became of the findings the previous review left behind.

A re-review ends with a `## Prior findings` ledger: one line per finding the
previous review reported, saying whether the change fixed it, left it open, or
declined it. The ledger is bookkeeping — it is stripped before the review is
published — and it is written by the agent that has just spent its attention on
the review itself, so it is the first thing to come up short.

Coming up short used to mean a line on stderr and nothing else. This module
replaces that with a disposition for every prior finding and a record of them
that outlives the run. A finding the ledger passes over is not automatically
unaccounted for: whether the file it names is still in the tree, and whether
the code it quotes is still in that file, are questions the worktree answers
without asking an agent anything. Only what neither the review nor the tree
settles is reported as undecided, and every record says which of the two
settled it, so an inference is never read back as a statement.

The record is a sidecar in the review directory rather than a section of the
review. Reconciliation parses its input for finding-shaped lines, so a
reconciliation written into the review would come back to the next round
looking like a fresh set of prior findings.

## Publishing

Everything that leaves the machine — review comments, replies, summaries, tracking issues — and the draft gate they all pass through first.

### pr_comments.py

PR comments lifecycle tracking.

Handles thread lifecycle state computation, local state persistence,
and GitHub data fetching for the pr-comments skill.

A thread's lifecycle state is what decides whether the run may report itself
done. `--post` is a request, not a guarantee: if triage routes any thread to
`needs_human` — contested, conflicting, a question, or too complex to
auto-fix — the fix pass *holds* publishing for the rest of the process, and the
hold outranks `--post`. Nothing reopens it (see `publishing`).

The fixes still get applied and still get committed. What waits is everything
that asserts the work is done: the push, the `Fixed in <sha>` replies, the
thread resolutions, and the summary. The commit sits locally with status
`push_held`, and `--finish --post` is what sends it:

```bash
pr comments --fix --post   # commits; holds the push, one thread is contested
# read the thread, answer the reviewer
pr comments --finish --post   # pushes, then drains the replies and the summary
```

Until that second command runs, the queue sits in state and the PR shows
nothing — an undelivered summary is indistinguishable from a run that had
nothing to say. `pr status` names it (`⚠ closeout owed: summary + 15 replies`)
and counts it as a merge blocker, so the hold survives the session that created
it.

This exists because threads are triaged independently. A reviewer saying "the
root cause you describe does not exist" removes that one thread from the
fixable set and leaves the pass free to fix, push, and report success on
everything else — 8 individually-real fixes pushed to a branch that had already
been superseded.

The halt is deliberately blunt: any open thread, not just a premise-invalidating
one. Telling those apart is the hard classification problem, and the cost of
being wrong is asymmetric — a needless hold costs one extra command, while a
missed one costs a pushed commit and a reply claiming work is done. Running
`--fix` and `--finish` in the same invocation does not defeat it: the discussion
is still open at both points, so the hold applies to both.

### pr_thread_models.py

Typed domain objects for PR review thread processing.

Persistence-oriented structures live in pr_domains.py; these model the
runtime pipeline: triage, classification, tracking, and fix-pass results.

### publishing.py

The gate every outward-facing write passes through.

A PR reply, a summary comment, a tracking issue — each one is visible to other
people the moment it lands, and a wrong one has to be retracted in front of the
reviewer. So the default is to draft: callers print what they would have sent and
report failure, and nothing leaves the machine until the entrypoint opts in.

One flag owns this for the whole process. Modules that write externally
(`pr_comments`, `review_issue`) ask here rather than carrying their own switch.

A hold overrides it. Some things a run learns mid-way — an unanswered question
about whether the work should exist at all — mean nothing more should leave the
machine, whatever the entrypoint was told. `hold` closes the gate for good, so
the two only ever compose in the safe direction.

What that means at the CLI: `pr comments` writes nothing outward unless you
pass `--post`. Replies, the fix summary, thread resolutions, deferral tracking
issues, the PR description, and the push are all printed to stderr as drafts
instead, prefixed `DRAFT (not published)`. Code fixes and the commit are
unaffected: they are local and undoable, and they are what makes the work
reviewable at all. The gate covers what leaves the machine.

A hand-written `pr comments --reply <id> --body-file <path>` is no exception: it
drafts the body and reports the draft, and only `--post` sends it.

Some comments are answered by rewriting the PR description rather than the code.
That is a GitHub write like any other, so the fix agent does not make it: it is
barred from running `gh` at all, and instead writes the replacement description
to `ignore/pr-comments/pr-description.md` in the worktree. The fix pass sends it
through the same gated client the replies use, which means a run without
`--post` records the intended edit and performs none. The undelivered
description is owed in `pr status` alongside the replies
(`⚠ closeout owed: PR description`) and `--finish --post` delivers it.

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
about. Nothing assumes a tracker: if `issue_tracker.provider` is unset for the
repo, a `--post` run asks where the repo files issues, then whether to record
the answer for this repo or for all of them. A repo-scoped answer is written to
`.workbench.yml` at the repo root — commit it and nobody is asked again. A
machine-wide answer goes to `config.yml` under the config root.

The question is only ever asked when it can be answered and the answer would
matter. A draft run does not ask, because it files nothing either way. A run
with no terminal at all — CI, or anything else detached from one — reports the
key to set instead of asking. A piped stdin is not that: the question goes to
the terminal the command was started from, so a `--post` run piped into `tee`
still asks. Either way an unanswered question files nothing: no tracking issue
is created and the deferral replies that would link to it are not sent, rather
than an issue being filed to a tracker nobody named.

### review_github.py

The review system's reads of a PR, and the GraphQL queries behind them.

PR metadata, the diff, the pending-review check, and the consolidated
review-thread query. Used by review_posting and review_dedup.

The transport is not here. ``gh_client`` owns running gh, the timeout tiers and
the rate-limit ladder; this module owns what the review system asks for and how
it reads the answer. Nothing here decides how a call is made, so a change to
retry or to a bound is made once, in the client, for every caller.

### review_issue.py

Issue tracking integration for claude-review.

### review_listing.py

The reviews listing that `pr review --list` serves.

The contract another repo reads review state through. It asks this CLI instead
of resolving `<state root>/reviews/<name>/review.md` from its own process and
scraping the prose, so what a consumer has to agree with us about collapses
from a state-root rung chain plus two directory-naming schemes plus the review
file's format down to this module's output.

Two properties are the reason the query beats the path derivation it replaces:

* **A wrong answer is loud.** Deriving a path and listing it cannot fail — a
  root nothing writes to reads exactly like a machine that has never run a
  review. Here the process is the error channel: a `pr` that is missing,
  crashes, or exits non-zero is "unknown", and an empty `reviews` array is the
  only thing that means "no reviews".
* **Nothing is re-derived.** Finding counts, verdict, pipeline status, cost and
  both timestamps are computed by the code that owns them and handed over
  whole, rather than parsed back out of a document written for a human.

A consumer asks for the row schema it speaks, and the CLI enforces it:

    pr review --list --schema-version 1

An unsupported value exits non-zero and names the versions this build serves. A
bare `pr review --list` writes a human table to stderr and nothing at all to
stdout — a consumer that forgets the handshake gets a `jq` parse failure rather
than a subtly-wrong document.

`stdout` carries one JSON object:

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
          "findings": {"must_fix": 0, "should_fix": 2, "nit": 1, "idiom": 0,
                       "total": 3},
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

A row reports its review's *path*, never its content: a consumer polling on an
interval would otherwise carry every review's full text on every tick. Finding
keys are the `SeverityConfig.json_key` vocabulary the rest of the codebase
already uses, so this document and `build_review_summary`'s cannot disagree
about what a severity is called. A review written before `meta.json` existed is
still listed, with an empty repo and a null PR number — unattributed is a fact
about that review, and dropping it would hide one the consumer can still open.

A missing reviews root is not an error; it is `{"reviews": []}` with exit 0.

**Version policy.** A new *optional* field does not bump `schema_version`. A
removed field, a renamed field, or a changed type adds a new version.
Enforcement comes from the supported set being allowed to *shrink* —
`--schema-version 1` keeps working until this build stops serving 1, and
`SCHEMA_VERSIONS` is the one place that says which those are. Nothing
hand-stamps a version into the document: the field echoes back what the caller
declared and this build agreed to serve, so it cannot go stale on its own.

### review_posting.py

High-level posting orchestration for review-post.

Handles chunked review submission, SHA-drift re-verification,
reclassification after LineResolutionError, dry-run display,
and post-tracking metadata.

## PR state

What a pull request is right now: its target, its threads, its CI, whether it has been pushed or rebased, and whether its reason to exist still holds.

### ci_failures.py

CI failure lifecycle tracking.

Handles failure classification, progression tracking, and rendering for the
ci-failures skill. State persistence is delegated to pr_domains.CIDomain.

### pr_context.py

Shared PR context resolution.

Resolves repo, branch, PR number, worktree root, and HEAD SHA once
per invocation. Replaces the duplicated discovery logic in ci-check,
review-threads, and the former review_common.detect_repo().

How much of that a command wants is one of three axes every `pr` subcommand
declares in its `_COMMANDS` entry in `ai/claude/bin/pr`. They are separate
because they routinely disagree:

| Axis | What it decides |
|---|---|
| **depth** | `ContextDepth.NONE` resolves nothing at all; `LOCAL` resolves from git alone; `REMOTE` adds the `gh` calls that name the repo and the PR |
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
`--pr <n>` escalates it to remote anyway — a PR number names a branch only `gh`
can report, and the branch is half the target key.

`review --list` is the only `NONE` one, and that is not "resolve less" — it is
"there is nothing to resolve". The listing answers from the user's own state
root, so it has no repo, no branch, and no target, and unlike `LOCAL` it works
from a directory that is not a git repository at all. `--pr` does not escalate
it: there is no target for a PR number to name at that depth, so honouring one
would spend a `gh` call on a value the handler never reads.

`review --list` is also the one invocation that writes no trail at all.
Resolving nothing and holding no lock is the shape of a query rather than of an
action, and the listing exists to be polled: the two records a dispatch writes
cost more than the query itself, and they land in the file every `otto-log`
query then reads. The exemption is read off these same three axes — `Need`
carries no trail flag of its own for a command to add itself to.

### pr_domains.py

The domains a PR's state is made of.

Each ``pr`` subcommand owns one domain and writes it as a unit. A domain is a
dataclass subclassing :class:`Domain`; subclassing is the registration, and
``pr_state`` derives its registry from ``PRState``'s own annotations, so a new
domain is added here and named there and nowhere else.

This module holds the domain types and the vocabulary they are written in.
``pr_state`` holds the envelope over them, the registry and the state file I/O,
and imports this module — never the other way round.

### pr_state.py

Unified PR state framework.

Provides a summary envelope over per-domain state files (CI failures,
PR comments, review artifacts). Each ``pr`` subcommand updates its own
section; ``pr status`` reads the whole thing without network calls.

State file: ``<state_dir()>/pr/<repo-key>-<branch-slug>/state.json``, keyed on the
run's target — see ``pr_target.target_dir``, which owns that path.

The domains this is an envelope over live in ``pr_domains``, which this module
imports and which never imports this one.

### pr_target.py

Where a run's bookkeeping lives, keyed by what the run targets.

A run's target is ``(origin repo key, target branch)``. Both components are
readable from a checkout with no network call, which is what lets the readers in
this repo agree on one directory without one of them having to ask the network:
the ``pr`` CLI resolving a PR it is about to review, and ``workbench-statusline``
rendering a prompt.

Two repos that share a repo key share one ``state.json`` and one ``run.lock``:
one run overwrites the other's state and serializes behind its lock, which is
the under-locking bug this layout exists to close. ``acme/api`` and
``other-org/api`` are the routine case, and every attempt to keep such pairs
apart by flattening the origin path into one component with a character map has
left another pair colliding — a lossy map cannot be injective, whatever the map.

So the key does not rely on the flattening for distinctness:

    key = <readable>-<digest>

The digest makes two different repos impossible to confuse. The readable part is
there only so a human reading ``pr/`` can tell which directory is which, which
frees it to be as lossy as flattening a path into one component requires.

The rule, in one paragraph because every reader here has to agree on it and a
rule that is hard to restate is itself a defect:

    Take the remote's **path**: for a remote that names a host (an explicit
    ``scheme://authority``, or scp-style ``host:path``), the path below the
    host; for a ``file://`` URL, the path below the authority; for a plain
    filesystem path, the whole string. Collapse repeated ``/`` and strip
    leading and trailing ``/``. Strip one trailing ``.git``, matching the
    suffix through the fold below, then strip trailing ``/`` again. For a
    ``file://`` URL or a filesystem path, keep the trailing segment alone.
    Fold the result: **map U+0041–U+005A to U+0061–U+007A and leave every other
    codepoint alone.** That is the **canonical form**; when it is empty there is
    no key — return ``None``. The key is ``slug(canonical)``, truncated to 64
    characters and stripped of trailing ``-``, then ``-``, then the first 8 hex
    characters of ``sha256(canonical.encode("utf-8")).hexdigest()``. When the
    readable part is empty, the key is the digest alone.

``slug(s)``, used above and again for the branch, is the whole of its own rule:

    Replace every run of one or more characters **outside**
    ``A-Z a-z 0-9 . _ -`` with a single ``-``, then strip leading and trailing
    ``-``. Nothing else — no case fold, and the dot and underscore survive. A
    mirror that guesses ``[^a-z0-9-]`` turns ``feat/v1.2`` into ``feat-v1-2``
    where this gives ``feat-v1.2``: two directories for one target, which
    under-locks every branch with a dot in its name.

Three properties of that rule a mirror has to reproduce exactly, because a run
that disagrees about any of them looks in a directory nobody writes:

* **A remote is hosted per its scheme, never per its authority.** ``file`` is
  never hosted, whatever authority follows it, because git ignores a file URL's
  authority and clones the path — ``file://localhost/srv/git/widget.git`` is the
  same clone as ``/srv/git/widget.git``. The scheme is matched
  case-insensitively and folded before that comparison, so ``FILE:///srv/repo``
  is unhosted exactly as ``file:///srv/repo`` is; a mirror testing
  ``scheme === "file"`` against the raw text calls it hosted and keeps the whole
  path. A remote naming no path names no repo.
* **Slashes are normalized on both sides of the ``.git`` strip.** git accepts
  ``https://github.com/acme/widget.git/``, whose trailing slash hides the suffix
  from a strip that ran first, and it accepts ``https://github.com/acme/widget/.git``,
  whose strip uncovers a trailing slash a pass that ran only first would leave
  behind. Normalizing once, on either side, gives one of those two spellings its
  own directory and its own lock.
* **The fold is codepoint arithmetic, not a call to a language's lowercase.**
  Repo paths are case-insensitive on GitHub and GitLab, so two differently-cased
  remotes are one repo; git refs are case-sensitive, so ``feat/A`` and ``feat/a``
  are two branches and the branch slug never folds. Deliberately *not*
  ``.toLowerCase()`` or ``.lower()``, on any subset of the input: the canonical
  form is what the digest hashes, so the fold has to be a pure function of
  codepoints and nothing else. A locale-sensitive variant such as
  ``toLocaleLowerCase`` folds ASCII ``I`` to ``ı`` under a Turkish locale, which
  would give ``acme/API`` two keys depending on where the process runs; and a
  Unicode-wide fold makes the key depend on the runtime's Unicode version, which
  is not the same across implementations (this runtime is Unicode 16.0, Node 22
  ships ICU 15.1). Restricting the fold to A–Z removes both channels: the 26
  codepoints it touches have meant the same thing in every Unicode version.

There is deliberately no second key format. An alternate PR-number key would be
a second source of truth for one target, and a transient ``gh`` failure could
move a live target between the two mid-flight.

The layout, which is this repo's own and is not reimplemented anywhere else:

    <state_dir()>/pr/<repo-key>-<branch-slug>/
        state.json
        run.lock

where ``<repo-key>`` is the key above and ``<branch-slug>`` is ``slug(branch)``.

``state_dir()`` rather than a literal path: the state root is relocatable, and
resolving through the function is what makes ``pr/`` ride along with a move
instead of being stranded at the old location.

That both components are derivable offline is a convenience for this repo's own
code, not an invitation to rebuild the path elsewhere: this module is the owner,
and another repo that wants to know what has been reviewed asks the CLI (see
``review_listing``) rather than deriving where a review would sit.

### push_status.py

Push domain — status rendering.

Detects unpushed commits by comparing local HEAD against the remote
tracking branch.  Computed at render time (no stored state needed).

### rebase_status.py

Rebase domain — status rendering.

Owns the display logic for RebaseSummary so the pr dispatcher
doesn't need to know rebase internals — including the phrase each refusal is
reported with, so a new refusal shows up by adding a row rather than by being
forgotten and rendering as a completed rebase.

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

### supersession.py

Whether a branch's reason to exist is already gone.

A branch can be rebased over a `main` that has deleted the code it was
fixing, and the reviewer's "this does not exist any more" is one thread among
ten. None of that needs an AI call to notice — the skew is in the commit dates,
the re-addition is in the diff, and the PR that removed it is one search away.

Three cheap checks, run by every branch-acting command before it acts:

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

This module answers the question; it does not decide what to do about it. The
two are separated because the callers legitimately differ. `pr comments` has
already spent its money by the time it publishes, so a positive verdict holds
the publishing — the same acts a contested thread's hold reaches: the push, the
replies, the resolutions, and the summary, but not the local commit. `pr review`
spends the largest budget of any command in the repo and the check runs before
the first agent call, so a positive verdict refuses before the spend rather than
after it, exit 4. One detection, two policies, each stated where the cost is.

The refusal prints the signals and writes the same JSON shape `pr rebase` uses
for its already-landed refusal, on the same exit code:

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

Distinct from `pr rebase`'s already-landed check, which asks whether the work
has *landed* rather than whether it has been *superseded*. Work can land
without the branch being superseded, and a branch can be superseded without its
commits having landed anywhere — someone solved the problem differently. They
stay separate: two of the landed check's three signals are local-only, and this
one makes a network call that a rebase should not have to pay for. They share
the exit code and the override flag, and nothing else.

## AI backends

The provider plumbing every AI call goes through — backend selection, streamed events, usage accounting, and quota.

### ai_backend.py

AI backend abstraction layer.

Dispatches preflight(), prompt(), invoke_agent(), and invoke_fix() to the
correct backend (Claude Code CLI or Pi CLI) based on AI_BACKEND env var.

Every entry point takes a required `cwd`, because a backend CLI inherits the
launching process's working directory unless it is told otherwise. An agent
given write access would then edit whichever worktree the session happened to
start in rather than the one being operated on. `add_dirs` is not a substitute —
it maps to `--add-dir`, which widens the set of directories the agent may touch
and has no way to narrow it. `prompt()` rejects the call at the signature,
`invoke_agent`/`invoke_fix` raise on an empty or non-existent `cwd`, and a test
fails the build on a new call site that omits it.

Every call made through here appends one record to the usage ledger, so what a
run cost is answerable without instrumenting the call site — see `ai_usage`.

### ai_backend_claude.py

Claude Code CLI backend for ai_backend.

Implements preflight(), prompt(), invoke_agent(), and invoke_fix() by
building `claude -p` commands and running them as subprocesses.

A failure is logged from whichever stream carried the detail, and from the exit
code alone when neither did. `claude -p` reports some failures on stdout with an
empty stderr — a usage limit is the common one — so a stderr-only error message
prints nothing at all and leaves the caller reporting a bare exit code with no
reason attached.

### ai_backend_events.py

Normalized event parsing for AI backend JSONL streams.

Provides a common StreamEvent and parsers for both Claude Code's
stream-json format and Pi's --mode json format, so stream_progress()
works identically regardless of backend.

### ai_backend_pi.py

Pi CLI backend for ai_backend.

Implements preflight(), prompt(), invoke_agent(), and invoke_fix() by
building `pi` commands and running them as subprocesses.

invoke_agent and invoke_fix use RPC mode (--mode rpc) for bidirectional control:
  - Budget enforcement via accumulated message_end costs + get_session_stats
  - Clean abort via {"type": "abort"} instead of SIGTERM
  - Claude-compatible result records written to session logs

prompt() uses print mode (pi -p) for simplicity.

Pi CLI reference:
  -p / --print     Prompt mode (non-interactive, like claude -p)
  --mode rpc       Bidirectional JSONL over stdin/stdout
  --approve        Auto-accept project trust (like claude --permission-mode acceptEdits)
  --no-session     Ephemeral mode (don't persist session)
  --tools <list>   Allowlist specific tools
  --model <id>     Model selection
  --thinking <lvl> Thinking depth: off, minimal, low, medium, high, xhigh
  --append-system-prompt <text>  Inject additional system prompt
  --verbose        Verbose output

Gaps vs Claude Code CLI:
  --max-turns      Not available; counted via turn_end events, abort via RPC
  --max-budget-usd Not available; tracked via message_end costs, abort via RPC
  --add-dir        Not available; directories passed in prompt text
  --agent          Not available; use --append-system-prompt with agent file contents

### ai_usage.py

AI usage accounting.

Parses cost and token usage out of backend session logs. Backend-neutral: the
Claude Code CLI and the Pi CLI both emit `result` records, in slightly different
spellings, and this module is the single place that reconciles them.

Lives below the review layer so ai_backend can depend on it without inverting
the dependency.

Every AI call made through the workbench appends one record to a monthly JSONL
file under `~/.local/state/workbench/usage/` — cost, tokens, cache hit rate, and
the task that made the call. Python entry points record automatically through
`ai_backend`; the two shell paths that cannot use it — `run-auto-task`, which
needs slash commands, and `AI_COMMAND`, which is pluggable — go through
`ai-usage-log`.

A call that reports no usage records nothing rather than a zero row. An
unmeasured call is then visibly absent instead of looking free, which a zeroed
row cannot be told apart from.

`otto-log stats` reads the ledger back. Its `--by model` breakdown shows cost
only, because the CLI reports cost per model but tokens per session — leaving the
token columns blank beats counting one session's tokens against every model it
used.

### vertex_quota.py

Vertex AI quota checks for the Claude Code backend.

Verifies that the models a run would use have quota allocations on the
configured Vertex AI project/region before any agent is spawned.  Catches
misconfigured model ids (nothing the project can serve, not even the model's
family) within ~1s instead of burning ~6 minutes on retries.

Reached through ``ai_backend.preflight()`` — nothing outside the Claude
backend should import this module.

## Evaluation

The eval harness: fixture tasks, the scorers that grade each task's output, and the aggregation the CI ratchet gates on.

### eval_scoring.py

Evaluation scoring, aggregation, and baseline comparison.

Task-agnostic: what a run *is* and how it is scored belongs to the task
(`eval_scoring_review`, `eval_scoring_cifix`, ...). What lives here is the shape
of a score, the statistics over repeated runs, and the baseline diff — the parts
every task shares.

`eval-models --compare` diffs a run against the baselines in `eval/results/` and
exits `2` on a regression. The gate is deliberately narrow, because a gate that
flaps gets disabled: token growth, quality drops and false positives fail past
the thresholds declared below, the cache-read ratio fails below its floor, and
cost and duration are reported but never gated.

Tokens are gated and cost is not because tokens are what a change controls; the
dollar figure also moves with model prices, and duration moves with machine
load. The cache-read floor is an absolute minimum rather than a delta: a
prompt-prefix change that silently disables caching shows up as the ratio
collapsing, and the value it collapsed from is not the interesting number.

A baseline written before a metric existed leaves it ungated rather than
failing, so an older baseline still loads. The comparison table marks every
metric `pass`, `fail`, or `ungated` — including the ones that cannot fail.

### eval_scoring_cifix.py

The ci-fix eval task: hand a failing repo to the fix agent, re-run the check.

There is no finding-matching here — the verify command is the oracle. A case is
either fixed or it is not, so what varies between models is the token cost of
getting there, which is the thing the CI ratchet gates on.

Each case ships a `reference-fix/` overlay: the same relative paths, already
corrected. It is not used at eval time; it exists so the test suite can prove the
case is solvable and the oracle is not vacuous, without spending a token. An
oracle that cannot fail, or cannot be satisfied, measures nothing.

Because CI failures are usually environment-shaped, these cases put stub
binaries on `PATH` rather than depending on what the host happens to have
installed, so they fail the same way everywhere.

### eval_scoring_review.py

The review eval task: run review-orchestrate, score findings against a manifest.

Everything here is specific to reviewing code. The runner, the fixture repo, and
the aggregation over runs live in `eval_task` and `eval_scoring` and know nothing
about findings.

A finding counts as matched when its path, severity, and description all line up
and its line range *overlaps* the manifest's `line_range` — not when its start
line falls inside the window. Reviewers routinely anchor a range at the
enclosing declaration, and containment scored those as a miss and a false
positive at once, penalising a correct finding twice.

A manifest's `false_positives_max` is a noise budget: findings outside every
expectation are counted, and a run over the budget is marked `(over budget)`
next to its FP count. It annotates rather than fails — `--compare` gates on
movement away from the baseline, so an absolute bar here would fire on cases
that have never met it.

### eval_scoring_skill.py

The skill eval task: drive a SKILL.md against a fixture, grade what it ran.

A skill is a procedure a session follows, not a subprocess, so there is no
artifact to diff. What there is, is the sequence of shell commands it issued —
and both skills covered here, `pr-comments` and `pr-rebase`, state their
constraints as commands not to issue ("never pass `--post` before the user has
seen the drafts", "never run raw `git push --force-with-lease`").

So the trace is the oracle. The harness puts recording shims on `PATH`, injects
the live `SKILL.md` body as the prompt, and scores what the session ran:
`requires` groups must appear **in order**, `forbids` groups must not appear at
all. Any violation drops precision to zero — a constraint is not something you
get partial credit for breaking.

The SKILL.md is read live from `ai/claude/skills/`, never copied into a case.
The file is the single source of truth; a copy would let the eval keep passing
against a skill that no longer says what the copy says. That is the point:
before this, there was no way to tell whether a change to a `SKILL.md` made the
skill better or worse.

A group matches a trace line when every one of its tokens **equals one of that
line's argv elements**. Whole elements, not substrings, and that is
load-bearing: the substring rule this replaced matched a `["git", "push"]` group
against the `git remote get-url --push origin` the Claude Code harness issues at
startup, zeroing precision on sessions that never pushed, and let
`["pr", "rebase"]` match the very `pr-rebase` script the skill forbids. For
matching only, an argv element is also split on its first `=`, so `--track=T-3`
and `--track T-3` grade the same — neither `--push` nor `pr-rebase` contains one,
so the split cannot reopen either case above.

Two things follow when authoring a case. A group is a *subset* of the line, so a
`requires` group is evidence of what ran and never of what did not — pair it with
a `forbids` group for each way the case could pass without being satisfied. And
the trace records the harness's own startup commands alongside the model's, so a
`forbids` group naming a git subcommand the harness issues for itself scores
precision 0.0 on a fully compliant session; forbid the operation the skill must
not perform (`["git", "push"]`, `["git", "rebase"]`) rather than the family it
belongs to.

The CLIs the skill drives are stubbed by `responses.json`, one key per binary
name, fail-closed by default so a fixture gap cannot read as a pass. A case opts
a binary into `on_no_match: "passthrough"` when it wants the real one — both
`pr-rebase` cases do this for `git`, because the fixture is a real repo and
`git status` should work there. A binary left out entirely is not intercepted at
all and no trace line is recorded for it, so a binary named as the leading token
of any group needs an entry here or the group can never be satisfied or violated.

Two limits worth naming. The trace cannot see obligations that are text-only,
such as `pr-rebase`'s instruction to report `files_stale` and tell the user to
regenerate those files by hand. And each case drives a single *user* turn, with
the user's side encoded in the scenario prompt — which covers both sides of the
`pr-comments` approval gate as two cases, but does not exercise a real multi-turn
exchange. Within that turn `SKILL_MAX_TURNS` and `SKILL_MAX_BUDGET` cap the
tool-call turns and the spend; a scenario needing more of either hits the cap
silently rather than completing.

### eval_task.py

Task-agnostic evaluation plumbing.

An eval case is a corpus directory with a manifest. The manifest's `task` field
picks how the case is run and scored; everything here is what is common to all
tasks — the fixture repo, the run options, the artifacts a run leaves behind.

The field is optional and defaults to `review`, so a manifest written before
tasks existed keeps working.

| Task | What the case holds | How it is scored |
|---|---|---|
| `review` | Source with planted defects, plus the findings expected of a reviewer | Recall, precision, and severity accuracy against those expectations |
| `ci-fix` | A repo whose check fails, plus a `verify` command | Binary — the check passes after the fix agent runs, or it does not |
| `skill` | A scenario, the `SKILL.md` to drive it with, and stubbed CLIs | The command trace — required calls in order, forbidden calls absent |

Every case needs a `src/` directory: it is copied into the throwaway git repo
that becomes the run's `cwd`, and a case without one is skipped.

`EVAL_CASE_BUDGET` bounds a single case's run. It is a deadline on work that
could reasonably keep going rather than a bound on a subprocess that should
already have answered, which is why it sits outside the `timeouts` table.

Task implementations live in `eval_scoring_<task>.py` and are resolved lazily so
that adding a task does not make every other task's dependencies load.

## Platform

The shared substrate — process execution, logging, the structured trail, serialization, config, paths, and the tool framework the CLIs are built on.

### gh_client.py

One way to run gh, and the reads every caller was hand-rolling.

`ai/` invoked `gh` as a literal argv head in 45 places across 13 files, and the
knowledge of how to do it well was spread so thin that most sites had none of
it. Eight had no timeout at all. Four returned `(exit_code, stdout)`, so the
stderr explaining a 5xx was discarded before any caller could render it. Retry
existed at one site out of forty-five, which is why a secondary rate limit
surfaced everywhere else as "no data" — indistinguishable from an empty result.

The runner is `run`, and `out`, `ok`, `lines` and `json_out` are the shapes
callers actually wanted from it. `api` and `graphql` sit above them for the
`gh api` surface, which is most of the traffic. Below all of it are the reads
that appeared at two or more call sites; a read used once belongs at its call
site, spelled out with `run`.

Retry is a property of talking to the API, so it lives with the calls that do:
`api`, `graphql`, and the reads above that resolve against GitHub rather than
against a local checkout. A caller driving an artifact download or reading
gh's own configuration gets no ladder, and should not.

The publishing gate is deliberately not here. `pr_comments` gates its writes on
`publishing.enabled()` at the call site and keeps doing so — a second implicit
gate inside the transport would make a policy decision invisible to the code
that owns it.

Unlike `git_client`, this depends on `log` as well as `proc`: a rate-limit
ladder that waits five minutes in silence reads as a hang, so the waiting is
announced. Whether a *failed* call is worth logging remains the caller's
decision, as it is there.

### git_client.py

One way to run git, and the reads every caller was hand-rolling.

`ai/` invoked `git` as a literal argv head in 131 places across 18 files, and
each one re-decided the same four things: whether to pass `-C` or `cwd=`,
whether to capture, whether a non-zero exit is a failure or an answer, and what
to do with stderr. The spread is why a fix applied to one call site — a
timeout, a retry, quoting non-ASCII paths — was never a fix for the other
hundred and thirty.

The runner is `run`, and `out`, `ok` and `lines` are the three shapes callers
actually wanted from it. Below them sit the reads that appeared at two or more
call sites — `head_sha`, `current_branch`, `is_dirty`, `commit_exists`; a read
used once belongs at its call site, spelled out with `run`.

| Call | What it gives you |
|---|---|
| `run(*args, cwd=, config=)` | The full `CmdResult`. Never raises on a non-zero exit — `diff --quiet`, `cat-file -e` and `rev-parse --verify` all answer a question with theirs. |
| `out(*args, default="")` | Stripped stdout, or `default` when git exited non-zero. |
| `ok(*args)` | Whether git exited cleanly, for the subcommands that answer a question that way. |
| `lines(*args)` | Stdout split into non-empty lines. |

There is no `timeout` parameter. The bound follows from the subcommand the same
way `core.quotePath` does — `fetch` takes `TRANSFER`, `worktree`/`commit`/`push`
run `UNBOUNDED`, and everything else is a flat-cost metadata read at `LOCAL` — so
the knowledge lives with the client that owns it rather than at every call site,
one of which used to pass a number of its own.

`config={"key": "value"}` becomes `-c key=value` ahead of the subcommand.
`diff`, `ls-files` and `status` get `core.quotePath=false` by default: git
escapes a non-ASCII path in that output unless told otherwise, and an escaped
name is not a pathspec a later `git add` can resolve — so a fix touching such a
file was staged as nothing and reported as applied. Applying the flag to the
subcommand rather than to each caller is what stops the next call site from
forgetting it.

Callers that still invoke git as literal argv are migrating across; a new one
should go through the client. The one difference to know before moving a call
site is that the client passes the worktree as `cwd` rather than as `git -C`, so
a root that does not exist raises `FileNotFoundError` out of Python before git is
reached rather than coming back as a non-zero exit that `out` and `ok` degrade
away. An absent worktree is a broken caller, not a question git declined to
answer — but a call site relying on the old degradation will start failing
loudly.

`out` returning `default` on a non-zero exit is the one place here that
discards a failure, and it is deliberate: it is what the wrappers it replaces
already did, because most of these reads are questions with a reasonable
"don't know" answer. When the exit code or stderr matters — and for a write it
always does — call `run` and read the `CmdResult`.

Writes are not modelled beyond `run`. Committing and pushing gets an owner of
its own, with the publishing gate over it, rather than a convenience wrapper
here that would turn four gate-less push sites into five.

Depends on `proc` and nothing else. Whether a failed read is worth logging is
the caller's decision, and most of them have already decided it is not.

### log.py

Centralized human-facing stderr output for otto-workbench AI scripts.

NOT for structured event logging — use trail.py for that.

### proc.py

One type for what a subprocess said, and one helper for running it.

`ai/` hand-rolls `subprocess.run(..., capture_output=True, text=True)` in
dozens of places, and each wrapper decides for itself what to keep. The ones
that return a positional tuple have no slot for stderr, so the cause of a
failure is gone before any caller can render it — `_gh_api` returning
`(returncode, stdout)` is why a 5xx from `gh` used to surface as an empty
message. Widening the tuple is not the fix: a caller reading fields by
name keeps working when a fourth thing needs carrying, and does not have to
learn the order.

`gh api` is the sharp case: it writes an API error body to stdout and its own
status line (`gh: ... (HTTP 503)`) to stderr, so a 404 is legible from stdout
while a 5xx or a dropped connection leaves stdout empty. A classifier reading
stdout alone calls that a success with no output.

So `run(cmd)` returns a frozen `CmdResult` carrying `returncode`, `stdout` and
`stderr`, and a caller reads what it needs by name:

| Read | What it gives you |
|---|---|
| `r.ok` | The command exited cleanly. |
| `r.detail` | `stderr` folded onto one line — what to quote in an error. |
| `r.combined_output` | Both streams, for classifying a failure by what it said. |
| `r.server_error` | The failure was a 5xx, so the remedy is to wait and retry. |

`failure_message(action, r)` renders a failure without asserting a cause the
code has not established: it names the action, appends whatever the command
said, and calls out a 5xx separately, deciding that from `server_error` so the
message and a classifier reading the same result cannot disagree about which
stream the evidence was on. It accepts a raw `subprocess.CompletedProcess` too,
so a call site still running `subprocess.run` directly can report a failure
without converting first.

An expired timeout is the same kind of answer. `run` converts it into a
`CmdResult` carrying `TIMEOUT_RETURNCODE` — the shell convention — with the bound
and the command quoted on stderr, and whatever the process wrote before it was
killed preserved on both streams. Raising instead would need a handler at each of
the call sites that has none; as a result code it degrades through
`out`/`ok`/`lines` exactly as any other failure does, and it is contract rather
than an implementation detail: the eval scorers tell a timed-out case from a
failed one by it.

Named `proc` rather than `cmd`: `ai/lib` goes on `sys.path` ahead of the
standard library, and a module called `cmd` there would shadow the stdlib
`cmd` that `pdb` imports.

Stdlib only, deliberately. This is the module everything else in `ai/lib`
should be free to depend on, and pulling in `log`, `ai_usage`, or
`workbench_paths` from here would make that impossible.

### run_lock.py

Advisory whole-run lock, scoped to what a run targets.

Two concurrent runs against one PR corrupt each other: they both
read-modify-write that target's ``state.json``, and with ``--fix`` they both
edit and commit the same checkout. This serializes them at the process level —
a second run refuses to start rather than interleaving.

The lock is keyed on the target, not the caller: ``pr review 2973`` from a repo
root and ``pr review --self`` from inside the PR's own worktree take the same
lock, while reviews of two different PRs launched from one directory take two.

Uses ``fcntl.flock`` on ``<target_dir>/run.lock``. The kernel drops the lock
when the holder exits for any reason, including SIGKILL, so there is no
stale-lock state to reap.

``claude-review`` (both its PR and its ``--self`` paths), ``ci-check`` and
``review-threads`` take the lock themselves, so invoking those three directly is
guarded too. When ``pr`` launched them they resolve the same target, compute the
same key, find it in ``WORKBENCH_RUN_LOCK`` and pass through as a no-op instead
of deadlocking against the lock their own parent holds.

That list is exhaustive, not an example: ``pr-rebase`` and ``pr-describe`` are
delegates that take no lock of their own, so running either directly is
unguarded and only ``pr rebase`` / ``pr describe`` serialize them.

### schema_gen.py

JSON Schema generation from Python dataclasses.

Produces JSON Schema from dataclass definitions, describing the documents
`serde` will accept for them. `serde.classify` owns what a type hint means;
this module only decides how each kind is written down, so the schema a model
reads and the reader that accepts the model's answer cannot disagree about
which shapes are legal. Both dispatch on `classify`'s one answer, so a new
`HintKind` fails a test in every module that has to handle it.

One case needs the dataclass's help. A class that reads more than one stored
shape through `_from_raw` — a legacy string, a renamed key — is the only thing
that knows what those shapes are, so it also defines
`_raw_schema(object_schema)`, returning the widened fragment. Without it the
published schema would call a document invalid that `serde` reads without
complaint; a test fails any `_from_raw` class in `ai/lib/` that does not define
one.

This is what fills the output schema half of a tool's `--tool-schema` contract —
see `tool_parser`.

### serde.py

Generic dataclass serialization with enum support.

Replaces hand-written _to_dict/_from_dict pairs. Uses dataclasses.asdict()
for serialization and type-hint-driven reconstruction for deserialization.

`classify` is the type-hint walk itself, exported because reading a value is
not the only thing that has to know what an annotation means — `schema_gen`
describes the same hints to a model and dispatches on the same answer.

### timeouts.py

How long a subprocess may run, decided once instead of at every call site.

`ai/` decided this in 22 places and four different ways: constants scoped to one
module, bare literals at the call site, a caller-supplied argument, and — for
most of the git client — nothing at all. The same operation got a different
bound depending on which file it was called from; a single `gh api` round trip
was 30s in `review_github`, 10s in `pr-rebase`, and 10s in `retro-scan`. No
principle separated those numbers. They were what whoever wrote each site
happened to pick.

Leaving the choice with callers is what produced the spread, so the fix is not a
better default for `timeout=` — it is taking the decision away from the call
site. A caller picks a tier that describes its operation; it does not pick a
number.

The tempting axis for those tiers is how long the work takes. The axis that
actually predicts the right answer is **what bounds the cost**:

| Tier | For | Why |
|---|---|---|
| `QUICK` | A `--value-flags` probe, a session hook reading one file | Should answer instantly; a breach is a wedged process, never real work. |
| `LOCAL` | Flat-cost local reads — `rev-parse`, `merge-base`, `log`, `grep`, `diff`, a `yq` parse | Scales with neither history nor tree size in any way that approaches the bound. |
| `NETWORK` | One round trip — a single `gh api` call, a tracker CLI, an HTTP request | Bounded by latency, not payload, so a breach means the far end stopped answering. |
| `TRANSFER` | Data-proportional over a socket — `fetch`, `gh api --paginate` | As large as the history or the result set, but a socket can stall in a way waiting will not fix. |
| `UNBOUNDED` | `worktree add`, `commit`, `push` | A bound would be wrong, not merely large. |

For the first three tiers the cost is the same whatever the repository holds, so
a breach means something is genuinely wrong — a hang, a dead socket, a deadlock —
and a timeout is a hang detector. For the last two the cost is whatever the input
costs, and a breach is indistinguishable from "the repository is large" or "this
repo's pre-commit hook runs a test suite". A fixed timeout there silently
converts a large repo into a broken tool, which is why `UNBOUNDED` exists and is
spelled out rather than omitted.

`bin/local/validate-timeouts` holds the table's monopoly: it rejects a numeric
literal and a bare `None` on any `timeout=` argument under `ai/`, and rejects a
`proc.run` or `subprocess.run` call that writes no `timeout=` at all. Reading
only the bounds that were written down left the omission invisible, which is the
case this table exists to eliminate — a call with no bound is indistinguishable
from nobody having thought about one. `ai/claude/mcps/server.py` is exempt: it
runs under `uv run` with `ai/lib` nowhere on `sys.path`, so it cannot import the
table.

Two numbers deliberately stay outside it. `ci-check --wait-timeout` and
`eval_task.EVAL_CASE_BUDGET` are deadlines for work that could reasonably keep
going, not bounds on a subprocess that should already have answered; they say
how long something is *worth*, which is a different question.

Stdlib-only and importing nothing, so that `proc`, `git_client`, and everything
built on them can depend on it without a cycle.

### tool_parser.py

ToolParser — drop-in argparse replacement with self-description.

Two hidden flags let one script read another's argparse parser rather than keep
a mirror of it. Both are answered here, and any script built on ``ToolParser``
supports both for free.

``--tool-schema`` emits a JSON document describing the tool's name, description,
input schema (derived from argparse actions), and output schema (explicitly
annotated). It is how the MCP server discovers tools — it probes every
executable in the workbench's component ``bin/`` directories, plus any
``tool_dirs`` adds — and it is what a skill's ``output_schema`` cites.

MCP discovery only probes scripts whose source names ``ToolParser`` or
``--tool-schema`` (see ``ai/claude/mcps/server.py``). A tool that implements
the protocol some other way will not be discovered. Naming the flag in a script
under one of those directories is therefore a claim, and
``bin/local/validate-tool-schema`` holds the build to it: it probes every
candidate discovery would and fails when one cannot answer.
``bin/local/validate-skills`` asserts the converse for the tool a skill's
``output_schema`` names — that one must implement the protocol whether or not it
carries a marker, or the skill cites a contract nothing publishes.

The output schema is generated from the tool's dataclass by ``schema_gen``,
which describes what ``serde`` will accept for each field rather than deciding
that for itself.

``--value-flags`` prints one option string per line: every option of that parser
that consumes a following value. ``pr`` asks a delegate this before deciding
whether a bare token is the command's target or some other flag's argument.
Without it, ``pr comments --reply 3777767789`` reads the reply ID as a PR number
and swallows it.

The two stay separate on purpose. ``--tool-schema`` is keyed by ``dest``, drops
``help=SUPPRESS`` actions, and loses option aliases, so arity cannot be
recovered from it faithfully — and declaring it also enrolls a script in MCP
discovery, which is not a side effect an arity probe should carry.

A delegate of ``pr`` that builds a plain ``argparse.ArgumentParser`` has to opt
in, by calling ``handle_value_flags(parser)`` before ``parse_args``. Skip it and
the parser rejects ``--value-flags`` as unknown, the probe exits non-zero, and
``pr`` falls back to its arity-blind scan — no error, just the occasional flag
value classified as the command's target. A ``ToolParser`` script answers the
flag without opting in.

One constraint comes with the protocol: every *option* the parser declares must
consume exactly one value. A flat list of option strings cannot express
``nargs='?'``, ``'+'``, ``'*'``, or an int above 1, so the probe refuses to
answer rather than report a wrong arity — it names the offending option on
stderr, exits 2, and ``pr`` reprints the message before degrading. Positionals
are unconstrained (``claude-review`` declares ``args`` with ``nargs='*'``).

Argparse introspection that reaches past the public API is collected here —
``value_taking_options`` and ``subparsers`` — so a caller never has to.

### tool_registry.py

What the tool registries say about the scripts an MCP client may reach.

The ``*/registry.yml`` files already document every workbench script: a
description, and for the ones meant to be reached directly a ``when_to_use``
and a ``usage`` line. ``visibility`` is the field that says who a tool is for —
``full`` and ``brief`` entries are rendered into the rules Claude loads,
``hidden`` ones are implementation details of another tool and are rendered
nowhere.

The MCP server reads the same field, so a tool hidden from a reader is also
absent from ``tools/list``. Registering a script is the act that offers it. The
alternative — every marker-bearing script exposed — puts ``ci-check``,
``pr-rebase`` and ``pr-describe`` in front of a client as peers of ``pr``, the
CLI whose subcommands run them, and a client picking between them is choosing
between a tool and its own internals.

Only ``meta.validation: bindir`` registries are read. That value is the
declaration that ``meta.source`` is a directory of executables with one
``tools[]`` entry per file, which is exactly the mapping wanted here, and
``bin/local/validate-registries`` already enforces both directions of it — so
the paths built below cannot drift from the files on disk.

### trail.py

Structured trail logging for otto-workbench AI scripts.

Every script appends to one root, ``workbench_paths.trail_dir()``, in a file
named for the emitting event's UTC month. Months past ``TRAIL_KEEP_MONTHS``
are dropped as runs start, so the root stays bounded whatever writes to it.
The --debug flag controls stderr echo only; whether a run is recorded at all
is the caller's ``record`` argument to ``Trail.start``.

One root for every script — ``~/.local/state/workbench/trail/YYYY-MM.jsonl``,
one file per month. ``otto-log recent --repo <org/repo>`` narrows it to one
repo; ``otto-log query --pr <n>`` finds every record for one PR, including the
terminal ``pr_outcome`` event ``pr gc`` writes when the PR merges or closes.

The root keeps six months, counting the month in progress
(``TRAIL_KEEP_MONTHS``). Every trail drops what falls outside the horizon as it
opens, so growth is bounded whatever writes to the root, and
``otto-log prune --keep <n>`` sweeps at a horizon you name when a machine is
short of space. A file whose stem is not a month — ``legacy.jsonl``, where the
cutover migration parked the pre-cutover history — is never dropped: its name
cannot place it in time, and nothing appends to it, so it is a fixed size
rather than a source of growth.

### workbench_config.py

The workbench's typed configuration.

One file per scope — global ``config.yml`` under the config root and project
``.workbench.yml`` at a repo root — deep-merged and typed into
``WorkbenchConfig``. The dataclasses here are the single definition: they type
the runtime lookups, they generate ``config.schema.json``
(``bin/local/generate-config-schema``), and their ``Phase``-keyed maps make a
phase a valid config key the moment it becomes an enum member.

The config is layers 4 and 5 of the precedence chain, behind CLI flags and env
vars:

    CLI flag > CLAUDE_REVIEW_<PHASE>_* > CLAUDE_REVIEW_* > project > global

so nothing here overrides a value a caller passed or exported.

### workbench_paths.py

Where the workbench keeps things.

Three user-level roots — config, state, and cache — each resolving through the
same chain:

    WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default

This module is the Python owner of those roots. Two other definitions express
the same chain and must stay in step: ``lib/constants.sh`` for shell, and
``zsh/config.d/aliases/docker.zsh``, which cannot source ``constants.sh`` at
shell startup. ``tests/workbench_roots.bats`` cross-validates all three.

Roots are resolved per call rather than frozen into module constants: the
environment is routinely set after import — by tests, and by callers that
re-point a root before invoking a subprocess — and an import-time constant
would capture whichever value happened to be live when the first importer
loaded this module.

### workbench_projects.py

The repos on this machine that use otto-workbench — Python half.

Membership means a workbench command actually ran in a repo. This side does the
recording for the tools written in Python: Claude's SessionStart hook, which
already resolves the repo root, and the ``pr`` CLI, which already resolves a
worktree root. ``lib/projects.sh`` is the shell half — it owns the one-time
backfill, the CLI, and the reads that the machine profile generator and the
project-scoped migrations make.

Both halves read and write one newline-delimited file of absolute paths, named
by ``workbench_paths.projects_registry()``. Text rather than YAML because every
write is an append and every read is a scan. ``tests/projects.bats``
cross-validates the two halves against the same file.

Nothing here raises. Registration is a side effect of a command that was run for
some other reason, and a hook that failed because a state file was unwritable
would cost the user their session for a bookkeeping entry.
