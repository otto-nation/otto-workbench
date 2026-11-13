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
  first edit
- End the prompt with the exact verification command and ask for its output. A
  subagent given the check reports its own result, which makes review faster
- Split a feature across dispatches by commit, not by phase. Observed 2026-11
  (grok through pi's `subagent` tool): a single prompt covering the wiring, CLI,
  docs and four test files for one commit hit `Request timed out` with most of the
  work uncommitted in the worktree; the retry, told to inspect `git diff` and
  finish, completed it. Brief a continuation with what is already done and what is
  missing. Revisit when the stall timeout or the model changes
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
  records drift (see `general.md` § Planning, 'Plan verified?')

## Where the related rules live

- Rebase and conflict policy: `git-operations.md` § Rebase, Cherry-Pick, Merge Conflicts
- Push policy: `git-operations.md` § Pushing
- Rebase output meaning: the `pr-rebase` skill
- Fix-pass claims: `self-review.md` § The Summary Is a Claim About the Tree
- Test strength: `testing.md` § A Test Must Fail When Its Subject Breaks
- Suite result reading: `testing.md` § Reading a Suite Result
- Cross-worktree and cwd: `git-operations.md` § Cross-Worktree Safety
