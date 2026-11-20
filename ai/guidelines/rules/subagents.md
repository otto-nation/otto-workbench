# Delegating to Subagents

The central rule for briefing, running, concurrently running, and reviewing AI
agents. Routing between models is the harness's job. Rebase-output, fix-pass,
and test specifics live with their owners (pointers below).

## Implementation

- One item per dispatch for implementers, each naming its file and the change;
  `thinking: medium` unless the item is a design decision. Tell the child to run
  a small command at least once a minute — a child that thinks silently past the
  stall timeout is killed with its work half done. Observed 2026-11 (grok through
  pi's `subagent` tool): a multi-item prompt at `thinking: high` went silent for
  over 300s and was killed (`Subagent produced no output for 300s`), once as
  `Request timed out`; the same work as one item per dispatch at
  `thinking: medium` finished every time
- Say what to change, not what to read. Telling a subagent to study a large
  reference file (a `.d.ts`, a generated schema) spends its budget before the
  first edit. When the work comes from a written plan, name the line ranges —
  "read ONLY lines 57-72 (conventions) and 165-208 (this task)" — rather than
  the file; it keeps the brief short without losing the conventions it must
  carry. It does not by itself keep a heavy item from stalling (next bullet)
- A short prompt is not a safe one: what stalls a child is how much one item
  carries. Observed 2026-11 (grok through pi's `subagent` tool, an eight-task
  plan run one task per dispatch): seven of ten dispatches finished first time at
  `thinking: medium` or `low`; the three that did not were one item — a
  70-line shell function with an `awk` parser, plus its tests, two revert checks
  and ten verify commands — and failed as `Subagent produced no output for 300s`
  twice and `Request timed out` once, at medium and at low, each with the
  once-a-minute instruction in the prompt, so that instruction does not prevent
  the stall. The retry that worked split by artifact: the code change in one
  dispatch, then tests, revert checks and the commit in the next. Split that way
  whenever an item holds more than one of new logic, new tests, revert checks, a
  long verify list. A stalled child may have left a finished edit (the third
  stall had); run the repo's own checks on it and finish a one-line failure
  yourself rather than redispatching. Revisit when the model or the stall
  timeout changes
- End the prompt with the exact verification command and ask for its output. A
  subagent given the check reports its own result, which makes review faster
- Split a feature across dispatches by commit, not by phase. Observed 2026-11
  (grok through pi's `subagent` tool): a single prompt covering the wiring, CLI,
  docs and four test files for one commit hit `Request timed out` with most of the
  work uncommitted in the worktree; the retry, told to inspect `git diff` and
  finish, completed it. Brief a continuation with what is already done and what is
  missing. Revisit when the stall timeout or the model changes
- A fully specified plan can be run several tasks to a dispatch. Observed 2026-11
  (grok through pi's `subagent` tool, `thinking: medium`): six tasks of an opus
  plan ran in one dispatch with no stall and one commit per task, then two more
  tasks and a test fix in the next. Each task in that plan gave its files, its
  failing test, its revert checks and its exact commit subject. A three-task
  dispatch of another such plan, with no progress instruction, was killed for
  300s of silence after committing all three. So read `git log` before
  redispatching a stalled multi-task child. This is the exception to the splits
  above, not a replacement: drop back to one item per dispatch as soon as an item
  carries new logic that is not already specified. Revisit when the model or the
  stall timeout changes
- A dispatch that fails with an empty `Subagent failed:` message is the service,
  not the task, once a trivial no-tools probe fails the same way. Observed 2026-11
  (grok through pi's `subagent` tool): three failures in a row, the last a
  one-word probe, with the worktree left clean each time. Stop retrying, do a
  small item directly, and revisit when the model or provider changes
- Never put a bare coreutils `timeout` around a long `pr review --self --fix`: a
  3000s bound killed one mid-fix, and a rerun was owed. Start it with `job_start`
  under Pi (`run_in_background` under Claude Code; see `general.md`) and a
  generous `timeout_minutes` (observed 2026-11: 10 to 35 minutes per
  run, longer when the repo's checks are slow)
- Retry a stalled or aborted call on the same model before changing models —
  the stall is usually the call, not the model. A killed or timed-out child can
  leave uncommitted partial files in the worktree; before retrying, read
  `git status` and `git diff` and tell the retry about them: finish or redo
  them, never silently discard, never trust them without re-running their tests
- A subagent drifts in small ways: an import moved, a colour or flag added, a
  branch skipped because it was "different". Read the whole diff. A passing
  check proves the code loads, not that it looks or behaves the same
- A subagent does not inherit the dispatcher's rules — they live in the
  dispatcher's context, not its own — so the task prompt carries every command
  and prohibition the work depends on. For a rebase that is
  `pr rebase --repo-dir <absolute worktree> --branch <branch> --fix`, a ban on
  `git rebase` and any raw `git push --force` / `--force-with-lease`, and a
  statement that `pr rebase` pushing the result is expected and needs no
  confirmation. Which tool a rebase request uses is
  `git-operations.md` § Rebase, Cherry-Pick, Merge Conflicts
- Two changes to the same subsystem can land in parallel under the same module
  name. Before a long rebase, `git diff <merge-base> origin/main --stat` over the
  directories the branch touches: a same-named module on main (observed 2026-11:
  `fix/resume.py`) is a design conflict that no conflict resolver, human or AI,
  settles by merging text. Decide the roles first, rename one side, then rebase
  by hand hunk by hunk. `pr rebase --fix` aborted on baseline.py and its earlier
  resolutions of settlement.py left dead code and a double settle
- Fixed mechanical commands (lockfile regeneration, codegen, `pr` subcommands with
  known flags) run directly under `job_start` rather than through an implementation
  subagent: observed 2026-11 through Pi's `subagent` tool, a `grok` dispatch given a
  mechanical regeneration task spent turns exploring the tree instead of running
  the command.
- In a multi-PR orchestrator session, delegate codebase inspections of unfamiliar
  modules to a read-only `gemini` subagent (`thinking: low`,
  `tools: ["read", "grep", "find", "ls"]`) so file reads do not accumulate in the
  orchestrator's context window (observed 2026-11 through Pi's `subagent` tool).
- The `gemini` shortcut is Flash — bulk extraction, `thinking: low`, read-only
  tools. Contested or architectural judgement is a separate pass: one read-only
  `google-vertex/gemini-4-neon` dispatch at `thinking: high`, not a 3-model
  fan-out. Observed 2026-11 through Pi's `subagent` / `consult` tools. Revisit
  when the session router or those tools change.

## Concurrency

- Several subagents may share one worktree only if exactly one of them writes.
  Reviewers are read-only and read the commits they were given by SHA
  (`git show <sha>:<path>`, `git grep -n <symbol> <sha>`), never the moving
  working tree, because the writer's later commits and dirty files change under
  them. The writer is told which paths it must not touch while a review runs,
  and stages by path (`git add <paths>`, never `git commit -a`) so a leftover
  file from another attempt is not swept into its commit. Two writers in one
  worktree race on the index and the branch
- A reviewer runs while the next task is implemented: each review finding is
  fixed in a later dispatch (batch it into the next implementer's prompt as a
  first step, with its own commit), so a finding never blocks the writer and
  never rides in an unrelated commit

## Audits

- Define the finding precisely ("an identical chain of 3+ builder calls", not
  "duplication"); a loose definition over-reports
- Require file:line evidence for every finding and cap the length — a finding
  without a line number is a guess
- Run the audit a second time after fixing what the first found. The repeat
  pass catches what the first pass's fixes left behind or exposed

## Reading the result

- The controller keeps the gates: validators, the selected tests, a revert
  check. A subagent's "passed" is a claim about its own run, in its own cwd
  (see `git-operations.md` § Cross-Worktree Safety)
- Look at the artifact — the diff, the screenshot, the output file — not the
  summary of it
- A red the child reports on a check that is green in your own shell is the
  child's environment until shown otherwise. Observed 2026-11 (grok through pi's
  `subagent` tool): `tests/bin_scripts.bats` came back red twice with
  `validate-test-deps: -h exit 1` in one child and green in another; the
  controller's runs were green every time and the validator exits 0 when run
  directly. Re-run it where you stand before chasing it, and tell the child not
  to edit anything to make it pass
- A child's own checks are not the push gate's. Observed 2026-11 (grok through
  pi's `subagent` tool): children wrote `.bats` suites and ran shellcheck only on
  the scripts, so the pre-push hook later refused the push over a bare `! grep`
  that Bats never fails on (SC2314, an assertion that could not fail) and unused
  variables read by a shared helper. Tell a child that writes shell tests to run
  `shellcheck --severity=warning` over every `.bats` file it adds
- After any subagent history rewrite (fold, rebase, squash), check
  `git diff --stat origin/main...HEAD` (three dots) and expect only the
  branch's own files: a subagent's `reset --soft origin/main` produced a
  commit that reverted a merged PR (see `git-operations.md` § Commit History)
- An AI-resolved merge is a claim, not a result. `pr rebase --fix` reports
  `files_one_sided` and `one_sided_regions` when it kept one side of a conflict;
  the other side's change is gone. What to do with that output is the
  `pr-rebase` skill's to say (see `ai/skills/pr-rebase/SKILL.md`). In this
  session it dropped three upstream behaviours and kept the first commit's
  message for 19 squashed commits
- A subagent's "revert check" that edits the expected value instead of the code
  under test proves nothing: ask for the subject line to be broken and restored
  by rewriting it (see `testing.md` § A Test Must Fail When Its Subject Breaks)
- Plans written by a model cite line numbers and a base that drift (an opus
  plan's citations were wrong the next day and its base had moved): every
  implementer's first task re-checks the plan's citations against HEAD and
  records drift (see `general.md` § Planning, 'Plan verified?'). Observed
  2026-11 (opus plan, grok implementers): citations were off by 1 to 10 lines in
  three of the six tasks implemented, and one pointed at a comment that did not say what the
  plan claimed. Implementers that re-checked and reported the drift lost nothing
- Have a read-only opus pass plan before implementers start, and treat its
  spec-issues list as a gate. Observed 2026-11 (opus through pi's `subagent`
  tool, writing only the plan file): handed an approved design, it returned 12
  issues before any code — the step the design extended ran from a directory with
  no worktrees and so removed nothing on any machine, the tool never removes the
  worktree it runs from, and "any reachable Docker daemon" would let a member of
  the `docker` group prune the shared root daemon. Put each proposed resolution
  to the user, and dispatch implementers only on the answers, because four of the
  twelve changed what had been approved

## Where the related rules live

- Rebase and conflict policy: `git-operations.md` § Rebase, Cherry-Pick, Merge Conflicts
- Push policy: `git-operations.md` § Pushing
- Rebase output meaning: the `pr-rebase` skill
- Fix-pass claims: `self-review.md` § The Summary Is a Claim About the Tree
- Test strength: `testing.md` § A Test Must Fail When Its Subject Breaks
- Suite result reading: `testing.md` § Reading a Suite Result
- Cross-worktree and cwd: `git-operations.md` § Cross-Worktree Safety
