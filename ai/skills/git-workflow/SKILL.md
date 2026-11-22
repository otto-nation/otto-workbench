---
name: git-workflow
description: "Git and PR procedures: creating and describing a PR with pr create, the PR template, rebasing with pr rebase, answering review comments, pushing to a branch that has an open PR, keeping a branch fresh, finishing a branch, diagnosing a failed git command, and analysing what a branch holds. TRIGGER when: about to open, describe, update or push to a PR, rebase or resolve conflicts, reply to review comments, start work on an existing branch, or debug a failed push or fetch. SKIP: the always-on prohibitions (never commit on main, never amend, never raw force-push), which are already in context."
source: otto-workbench/ai/skills/git-workflow/SKILL.md
kind: rule
invocation: "/git-workflow"
trigger: "Use before opening, describing or pushing to a PR, rebasing, answering review comments, starting work on an existing branch, or debugging a failed git command."
skip: "Do not use for the always-on prohibitions, which are already in context, or for work that touches no branch or PR."
---

# Git — Workflow

The procedures for getting a branch to `main`. The prohibitions that apply to every
session (worktree-first, protected branches, no amend, pushing) stay in
`git-operations.md` and are always loaded.

## PR Creation

- Always use `pr create --draft` to create a PR — `--draft` ensures PRs go through review before being marked ready. To see the title and body it would use without pushing or creating anything, add `--dry-run`. `--no-issue` is still accepted but inert now that nothing prompts. It stays accepted rather than removed because it is already written into invocations outside this repo, where rejecting it would fail the PR over a flag that asks for the behaviour now on by default
- `pr create` refuses when no self-review covers HEAD — run `pr review --self --fix` first (`self-review/SKILL.md` § Before PR Creation). Only when the user explicitly asked to skip: `pr create --draft --skip-review "<their reason>"`. The reason is required and recorded on the trail. `--dry-run` reports without refusing
- To close an issue on merge: `pr create --draft --closes 941`. This is the only thing that writes a closing keyword into the body — a PR that does not pass it closes nothing, however its branch is named. Repeat the flag for several issues
- A numeric ID is a GitHub issue and always closes. A tracker key (`ENG-123`) is accepted only where `issues.provider` is `linear`, and refused elsewhere rather than opening the PR with a dead link
- To give the description context about an issue without closing it: `pr create --draft --issue ENG-123` — the ID reaches the AI prompt and nothing else
- To push past a pre-push gate failure you have already read: `pr create --draft --no-verify`. For a flake, or a failure the branch did not cause — confirm which by running the failing check on the default branch first. It skips the *hook*, not the review: the self-review gate still applies, and `pr review --self --fix` is still owed
- To supply a custom title and/or body: `pr create --draft --title "feat: title" --body "description"` — when both are provided, AI generation is skipped entirely; when only one is provided, the other is still AI-generated. Use `--body-file /path/to/file` instead of `--body` for multi-line or complex content
- To target a branch other than the repo's default branch: `pr create --draft --base <ref>`
- When the current working directory is not the target repo (e.g., running from a different worktree), name the target: `pr create --draft --repo-dir /path/to/worktree`, or `pr create --draft --branch <name>` for a branch checked out in a sibling worktree — without one, git commands run against the CWD repo instead of the intended one

## The PR Template

- The SessionStart context line answers which template this repo ships and what sections it requires: a repo with one shows `PR template: {path} ({sections})`, with deletable sections after `optional:` when any exist; a repo without shows `PR template: none in this repo — use the fallback (## Summary, ## Changes, ## Testing)`. Read it rather than grepping `.github/` — the line and `pr create` resolve through the same `ai/lib/core/pr_template.py`, so the path and sections it names are the ones the automation found
- What the line says and what `pr create` *enforces* are not the same thing. A body is checked against the template's required headers only where the repo ships one: `pr.create_content.missing_sections` returns nothing when there is no template, so the fallback sections are what to write, not a gate that will stop you. A section whose HTML comment says to delete or remove it may be omitted when empty; required headers must stay. A required header is present only as its own heading line (trailing whitespace ignored; `## Link ENG-123` still counts as `## Link`). A prose mention of `## Why`, or a nested `### Follow-ups` heading, does not satisfy `## Why` / `## Follow-ups`
- When no such line is in your context, the harness you are running in has no SessionStart hook — that is Claude Code's, and its absence says nothing about the repo. Check the six paths below, then stop; that is a bounded check, not the open-ended hunt the line exists to save
- Six paths are resolved, in this order: `pull_request_template.md` in `.github/`, then the repo root, then `docs/`, each in either case. The first that exists wins, and `ai/lib/core/pr_template.py` owns the list for every caller that resolves it in code — bash included, which asks it rather than globbing
- Those six are GitHub's single-file form, not everything GitHub honours. A `PULL_REQUEST_TEMPLATE/` directory of several templates, and the `.txt` spelling, are both real and neither is resolved — GitHub picks between multiple templates by query parameter, which a resolver reading a checkout cannot do. In a repo using that form the line will say there is no template while one exists, so trust the directory over the line when you find one
- There is no config key for this and there should not be. The template is six `stat` calls away in the checkout, and a path recorded in `.workbench.yml` is a second answer that goes stale the moment someone adds, moves, or deletes the file

## Replying to Review Comments

- Back every factual claim in a reply with a link to the code. Use a blob permalink pinned to a SHA (`https://github.com/<owner>/<repo>/blob/<sha>/<path>#L<line>`) or a commit link — never a branch-relative URL, which drifts as the branch moves
- Answer review threads through `pr comments` (`--fix`, `--reply`, `--finish --post`) — never a bulk-reply tool, a raw `gh api` call, or a manual resolve. A round answered around it leaves nothing in the state `pr comments` keeps, so neither `pr comments` nor `pr batch` ever posts that round's summary
- A defect in what `pr comments` generates (a link that 404s, a path without its repo prefix, a wrong attribution) is fixed in the tooling and the round re-run. It is never reported to the user as something for them to fix, and never patched by hand in the posted comment
- A round with nothing contested closes itself with `--finish --post`; the user is asked only for decisions
- This applies hardest to disagreement. Telling a reviewer their premise is wrong, or that the code already does what they asked, is a claim about their reading of the code; if you cannot point at the line that settles it, you do not have the claim — say what you checked and ask
- The same holds for summary comments: every row that reports an outcome should link to the code it reports on

## Branch Freshness

- Before starting work or creating a PR on an existing branch, rebase onto `origin/main` with `pr rebase` (`git-workflow/SKILL.md` § Rebase, Cherry-Pick, Merge Conflicts) and verify the diff (`git diff --stat origin/main...HEAD`) contains only your changes — reversions indicate a stale base
- If a rebase has conflicts, resolve them before writing new code — don't add commits on top of a stale base
- `wt switch -c` brings the default branch up to date before it cuts the new one, through the `fetch-default` pre-switch hook `otto-workbench sync git` installs. When it cannot — the default branch has diverged from `origin`, or the worktree holding it will not fast-forward — the switch aborts instead of branching from a stale base. That abort is the guarantee working; fix the default branch rather than re-running with `--no-hooks`

## Branch Completion

- When implementation is complete, create a PR — never present a menu of completion options. Whether to stop and ask before the draft is `self-review/SKILL.md` § Before PR Creation. See PR Creation for the full invocation
- When the user states a specific next step ("merge this", "push it", "just keep it"), execute it directly. If it fails, debug the failure — don't fall back to an options menu
- Open the PR when the work is done, not when it is nearly done. Opening one is a claim that the branch is finished, and it is acted on as such: a reviewer reads it, marks it ready, and merges. Anything still on your list — a test you meant to add, a finding you meant to fix — belongs in the branch before the PR exists, or in a follow-up PR after it

## A Branch With an Open PR Is Shared

Once a PR exists, the branch is no longer yours alone. Someone may be reading it, may have marked it ready, may be merging it this second. A push that would have been routine an hour earlier is now a change to something another person is acting on.

- Prefer not to push to a branch whose PR is open. Land the follow-up work as its own PR instead. This is the default, not the fallback
- A push the `pr` tooling makes — `pr batch` publishing, `pr comments --post`, `pr review --self --fix --push`, `pr rebase` — needs no comment from you. Where the tooling has something to say, its summary comment and thread replies are the announcement; a fix commit or a rebase it pushes shows in the PR timeline. Never add a free-form comment of your own on top (`gh pr comment`, a PR-comment tool): it restates what the tooling posted, or what the timeline already shows, in prose no run can trace or update
- A push you make by hand while answering a review goes through `pr comments` too (`--settle` records it, `--finish --post` publishes the summary and replies). That run is the announcement; never add a free-form PR comment on top. A hand push for another reason (CI fix, a reviewer's request outside a comments round) needs a comment naming what changed and why
- Never hand-push to a PR that has been marked ready for review without saying something first. Ready is the author declaring the branch finished; a later push retracts that declaration, and only a comment tells the reviewer it was retracted
- A push that loses a race with a merge is not a git problem to retry. The branch is gone because the work landed: check whether your commit made it in (`git log --oneline origin/main..HEAD`), and open a follow-up PR for whatever did not. `cannot lock ref` on push usually means exactly this, not a broken remote
- The pre-push hook says when the branch it is pushing has an open PR, and whether it is marked ready — so on this machine the check is already made for you. It is a notice and not a gate, and it is best-effort: no `gh`, no network, or a slow forge leaves the push silent. When it says nothing, that is not the same as there being no PR

## Git Failure Debugging

When a git command fails (push, fetch, pull, clone), diagnose in this order:

1. **Hooks** — `git config core.hooksPath`, run the hook directly
2. **Config** — signing, credential helpers, push settings
3. **Connectivity** — `ssh -T git@github.com` or HTTPS auth; verify remote URL
4. **Ref state** — `git status`, upstream tracking, `git ls-remote origin refs/heads/<branch>`
5. **Surface** — report findings and the fix; if unresolved, ask the user to run the command with `!`

Step 4 takes the full ref on purpose. `git ls-remote` matches a bare branch name against any
ref whose tail equals it, so `feat/auth` also returns `refs/heads/user/feat/auth` — two rows,
and a caller reading the first gets a sibling branch's SHA. Always query `refs/heads/<branch>`.
Never complete a short SHA to 40 hex by hand; resolve it with `git rev-parse <short>` or
`git -C <worktree> rev-parse <short>`.

## Branch Analysis

When analyzing what a branch contains vs main, use exactly two commands:

1. `git log --oneline origin/main..origin/<branch>` — two dots for log (commits on branch not on main)
2. `git diff --stat origin/main...origin/<branch>` — three dots for diff (changes from merge-base only)

- Never use two-dot diff — it includes main's changes and produces false noise on stale branches
- If three-dot diff is still noisy, the branch needs a rebase — don't filter the noise, fix the source

## Rebase, Cherry-Pick, Merge Conflicts

- During interactive git operations (rebase, cherry-pick, merge conflict resolution), use only Bash commands — the Edit tool can corrupt git's index state and abort the operation
- Any request to rebase a branch, update it onto its base, or squash-and-rebase it — however worded, including when the work is delegated to a subagent — is `pr rebase`. Pass `--fix` from the start unless the user asked for no AI; `--no-push` only when they ask to withhold the push. Wording such as "by hand" or "hand rebase" says who resolves conflicts (the agent, which is `--fix`), not which tool. A hand-driven `git rebase` is only for what the CLI cannot do (an interactive reorder or edit, a cherry-pick) or when the user names `git rebase` / interactive rebase explicitly. A delegated rebase's task prompt carries that exact command per `subagents/SKILL.md` § Implementation
- Without `--fix`, `pr rebase` rebuilds declared generated files and lockfiles, then aborts to a clean tree (exit 3) on any other conflict; pass `--stop-on-conflict` to keep the paused tree, or `--fix` for AI resolution. `--fix` takes a file whole only where that is safe (generated files, regenerable lockfiles) and merges everything else hunk by hunk
- There is no `--squash` flag. An explicit squash the user asked for is `git reset --soft` of the branch's own commits to one commit, then `pr rebase` (never `git rebase -i`). The CLI's own automatic squash is the `pr-rebase` skill's to describe. After a replay that conflicted commit-after-commit on the same paths — a rename-then-pointer history, or a stacked PR whose own commits re-conflict on paths the parent did not change — squash the same way (`git reset --soft "$(git merge-base HEAD origin/<base>)"` then `pr rebase --fix`). Auto squash does not cover those cases
- `pr rebase --fix` does not report a rebase complete when a file it resolved no longer parses. After the AI answer's conflict markers check out, Go (`gofmt -e`), Python (`ast.parse`), shell (`bash -n`), JSON, YAML, and TOML are checked before the file is written or staged. A failure is an unresolved conflict (exit 3, rebase left in place), not a push command. Unknown suffixes and missing checker binaries pass rather than blocking the rebase
- Never resolve a conflict in a hand-written file with a whole-file `git checkout --ours`/`--theirs`, or by copying either side's file over it. Taking a side replaces every hunk in the file, including the ones git had already merged cleanly, so the replayed commit's other edits to it vanish with no marker and no error. During a rebase `--ours` is the branch being rebased *onto*, so `--ours` throws away the branch's own work. Comments that explain the step correctly do not make it safe
- To undo a whole-file take before continuing, `git checkout -m -- <file>` restores the conflict markers and the three index stages. Resolve the restored conflict hunk by hunk
- A codemod branch that is rebased by re-running its scripts must hold every edit in those scripts. That includes a one-off `sed`, an inline Python fix, or a hand edit: put it in a script or a `.patch` under the workspace, or the next rebase loses it
- The global `prepare-commit-msg` hook refuses to conclude a conflicted rebase or cherry-pick step whose resolution drops a change git had merged cleanly. `post-rewrite` reports any commit a rebase dropped while its changes were missing from the result. When the hook refuses, restore the conflict with `git checkout -m`. Set `WORKBENCH_ALLOW_DROPPED_CHANGES=1` only after confirming that each listed change really is superseded
