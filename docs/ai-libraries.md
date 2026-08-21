---
title: AI Libraries
description: Every module in ai/lib/ — the Python behind pr review, pr comments, pr ci, and the eval harness.
---
<!-- Generated from docs/ai-libraries.src.md by bin/local/compose-docs — do not edit. -->

# AI Libraries

Every module in `ai/lib/`, grouped by what it is for. This is the reference; [AI Automation](ai-automation) is the guide, and holds the setup, the configuration, and the flow that crosses several of these modules at once.

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

### review_gc.py

Removal of review artifacts, at every lifecycle that removes one.

Three sweeps, one module, so that nothing else in the review system deletes a
review's files: the sweep at the end of a successful run (`cleaned_on_success`),
the stale-intermediate sweep and orphan collection `pr gc` runs, and the prune
of reviews whose PR has been merged or closed.

They differ only in what makes a file collectable — the run being over, age, or
the PR being gone — and all of them read what a review directory holds from
`review_common.phase_artifacts` rather than naming files themselves.

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

### review_dedup.py

Deduplication of findings against already-posted PR comments.

Fetches existing bot comments (inline and review-body), compares via
Jaccard similarity, and filters out duplicates before posting.

### review_disprove.py

Disprove-it gate: adversarial falsification of review findings.

After synthesis, each Must-fix and Should-fix finding is challenged.
Findings that cannot survive scrutiny are dropped before posting.

### review_findings.py

Finding parsing, renumbering, deduplication, verification, and stable IDs.

Shared between review-orchestrate (merging/verification) and review-post (parsing).

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

### pr_thread_models.py

Typed domain objects for PR review thread processing.

Persistence-oriented structures live in pr_state.py; these model the
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

### review_github.py

GitHub API primitives, retry logic, and PR metadata fetching.

Low-level wrappers around ``gh api`` with rate-limit handling and
exponential backoff.  Used by review_posting and review_dedup.

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

A row reports its review's *path*, never its content: a consumer polling on an
interval would otherwise carry every review's full text on every tick.

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
ci-failures skill. State persistence is delegated to pr_state.CIDomain.

### pr_context.py

Shared PR context resolution.

Resolves repo, branch, PR number, worktree root, and HEAD SHA once
per invocation. Replaces the duplicated discovery logic in ci-check,
review-threads, and the former review_common.detect_repo().

### pr_state.py

Unified PR state framework.

Provides a summary envelope over per-domain state files (CI failures,
PR comments, review artifacts). Each ``pr`` subcommand updates its own
section; ``pr status`` reads the whole thing without network calls.

State file: ``<state_dir()>/pr/<repo-key>-<branch-slug>/state.json``, keyed on the
run's target — see ``pr_target.target_dir``, which owns that path.

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

So the key does not rely on the flattening for distinctness::

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

The layout, which is this repo's own and is not reimplemented anywhere else::

    <state_dir()>/pr/<repo-key>-<branch-slug>/
        state.json
        run.lock

where ``<repo-key>`` is the key above and ``<branch-slug>`` is ``slug(branch)``.

``state_dir()`` rather than a literal path: the state root is relocatable, and
resolving through the function is what makes ``pr/`` ride along with a move
instead of being stranded at the old location.

### push_status.py

Push domain — status rendering.

Detects unpushed commits by comparing local HEAD against the remote
tracking branch.  Computed at render time (no stored state needed).

### rebase_status.py

Rebase domain — status rendering.

Owns the display logic for RebaseSummary so the pr dispatcher
doesn't need to know rebase internals.

### supersession.py

Whether a branch's reason to exist is already gone.

A branch can be rebased over a `main` that has deleted the code it was
fixing, and the reviewer's "this does not exist any more" is one thread among
ten. None of that needs an AI call to notice — the skew is in the commit dates,
the re-addition is in the diff, and the PR that removed it is one search away.

This module answers the question; it does not decide what to do about it. The
two are separated because the callers legitimately differ. `pr comments` has
already spent its money by the time it publishes, so a positive verdict holds
the publishing. `pr review` spends the largest budget of any command in the
repo, so a positive verdict refuses before the spend rather than after it. One
detection, two policies, each stated where the cost is.

Distinct from `pr rebase`'s already-landed check, which asks whether the work
has *landed* rather than whether it has been *superseded*. Work can land
without the branch being superseded, and a branch can be superseded without its
commits having landed anywhere — someone solved the problem differently. They
stay separate: two of the landed check's three signals are local-only, and this
one makes a network call that a rebase should not have to pay for.

## AI backends

The provider plumbing every AI call goes through — backend selection, streamed events, usage accounting, and quota.

### ai_backend_claude.py

Claude Code CLI backend for ai_backend.

Implements preflight(), prompt(), invoke_agent(), and invoke_fix() by
building `claude -p` commands and running them as subprocesses.

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

### ai_backend.py

AI backend abstraction layer.

Dispatches preflight(), prompt(), invoke_agent(), and invoke_fix() to the
correct backend (Claude Code CLI or Pi CLI) based on AI_BACKEND env var.

### ai_usage.py

AI usage accounting.

Parses cost and token usage out of backend session logs. Backend-neutral: the
Claude Code CLI and the Pi CLI both emit `result` records, in slightly different
spellings, and this module is the single place that reconciles them.

Lives below the review layer so ai_backend can depend on it without inverting
the dependency.

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

### eval_scoring_cifix.py

The ci-fix eval task: hand a failing repo to the fix agent, re-run the check.

There is no finding-matching here — the verify command is the oracle. A case is
either fixed or it is not, so what varies between models is the token cost of
getting there, which is the thing the CI ratchet gates on.

Each case ships a `reference-fix/` overlay: the same relative paths, already
corrected. It is not used at eval time; it exists so the test suite can prove the
case is solvable and the oracle is not vacuous, without spending a token.

### eval_scoring_review.py

The review eval task: run review-orchestrate, score findings against a manifest.

Everything here is specific to reviewing code. The runner, the fixture repo, and
the aggregation over runs live in `eval_task` and `eval_scoring` and know nothing
about findings.

### eval_scoring_skill.py

The skill eval task: drive a SKILL.md against a fixture, grade what it ran.

A skill is a procedure a session follows, not a subprocess, so there is no
artifact to diff. What there is, is the sequence of shell commands it issued —
and both skills covered here state their constraints as commands not to issue.
So the trace is the oracle: stubs on PATH record every call, and the manifest
declares which groups of tokens must appear, in order, and which must not.

The SKILL.md is read live from `ai/claude/skills/`, never copied into a case.
The file is the single source of truth; a copy would let the eval keep passing
against a skill that no longer says what the copy says.

### eval_scoring.py

Evaluation scoring, aggregation, and baseline comparison.

Task-agnostic: what a run *is* and how it is scored belongs to the task
(`eval_scoring_review`, `eval_scoring_cifix`, ...). What lives here is the shape
of a score, the statistics over repeated runs, and the baseline diff — the parts
every task shares.

### eval_task.py

Task-agnostic evaluation plumbing.

An eval case is a corpus directory with a manifest. The manifest's `task` field
picks how the case is run and scored; everything here is what is common to all
tasks — the fixture repo, the run options, the artifacts a run leaves behind.

Task implementations live in `eval_scoring_<task>.py` and are resolved lazily so
that adding a task does not make every other task's dependencies load.

## Platform

The shared substrate — process execution, logging, the structured trail, serialization, config, paths, and the tool framework the CLIs are built on.

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
call sites; a read used once belongs at its call site, spelled out with `run`.

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
which shapes are legal.

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

Operation-bounded work — `git rev-parse`, one `gh api` round trip, a `yq` parse
— costs the same whatever the repository holds. Exceeding the bound means
something is genuinely wrong: a hang, a dead socket, a deadlock. A timeout is a
hang detector here, and a tight one is correct.

Data-bounded and user-bounded work — `git worktree add`, `fetch --unshallow`,
`gh api --paginate`, `git commit`, `git push` — costs whatever the input costs.
Exceeding the bound is indistinguishable from "the repository is large" or
"this repo's pre-commit hook runs a test suite". A fixed timeout there silently
converts a large repo into a broken tool, which is why `UNBOUNDED` exists and is
spelled out rather than omitted.

Stdlib-only and importing nothing, so that `proc`, `git_client`, and everything
built on them can depend on it without a cycle.

### tool_parser.py

ToolParser — drop-in argparse replacement with self-description.

Scripts that use ToolParser automatically support ``--tool-schema``,
which emits a JSON document describing the tool's name, description,
input schema (derived from argparse actions), and output schema
(explicitly annotated).

MCP discovery only probes scripts whose source names ``ToolParser`` or
``--tool-schema`` (see ``ai/claude/mcps/server.py``). A tool that implements
the protocol some other way will not be discovered.

This module also provides ``handle_value_flags``, a lighter probe that answers
which of a parser's options take a value.  ToolParser scripts inherit it;
plain-``argparse`` scripts opt in with one call.  See its docstring for why the
arity question is not answered out of ``--tool-schema``.

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
