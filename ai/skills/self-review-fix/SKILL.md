---
name: self-review-fix
description: "Run self-review and auto-fix findings. Wraps pr review --self --fix --push. Can also fix from an existing review without re-running. TRIGGER when: user asks to self-review a branch, run pre-merge review, or auto-fix findings before PR creation. SKIP: reviewing someone else's PR (use code-review or review); addressing existing PR review comments (use pr-comments)."
source: otto-workbench/ai/skills/self-review-fix/SKILL.md
invocation: "/self-review-fix [branch_name]"
trigger: "Use when the user asks to self-review a branch, run a pre-merge review, or auto-fix review findings before creating a PR."
skip: "Do not use for reviewing someone else's PR (use code-review or review instead). Do not use for addressing existing PR review comments (use pr-comments instead)."
---

# Self-Review Fix

Reviews a branch and automatically applies fixes for the findings.

---

## Arguments

- `branch_name` (optional): Branch to review. Defaults to the current branch.
  Required when the CWD is a bare repo (no HEAD to detect).

---

## How It Works

1. Determine the branch name (from argument or current HEAD)
2. Check if a self-review already exists for the current repo and branch in
   `~/.local/state/workbench/reviews/`
3. If no review, or the review is stale, run `pr review --self --fix --push`
4. Report what was fixed and what was skipped — never ask, never fix manually

---

## Steps

### Step 1: Determine branch name

Get the branch name — use the skill argument if provided, otherwise:
```bash
git rev-parse --abbrev-ref HEAD
```

The `pr` script handles branch resolution (fuzzy matching, worktree lookup)
internally via `pr_context` — pass the argument through directly.

### Step 2: Check for existing review

1. Get the repo name:
   ```bash
   git remote get-url origin | xargs basename -s .git
   ```

2. Sanitize the branch name and check for the review file:
   ```bash
   echo "<branch_name>" | tr '/' '-'
   ```
   ```bash
   ls ~/.local/state/workbench/reviews/<repo>-self-<sanitized>/review.md
   ```

If the review file exists, read it with the Read tool. Extract the
`<!-- head_sha: -->` value and compare against the **resolved branch** HEAD:
```bash
git rev-parse <branch_name>
```

- **SHA matches**: Count unchecked findings (`- [ ]`).
  If unchecked findings exist, go to Step 3.
  If all findings are checked, report "all findings already addressed" and stop.
- **SHA doesn't match**: The review is stale. Go to Step 3.
- **No review file**: Go to Step 3.

### Step 3: Run pr review

```bash
pr review --self --fix --push --branch <branch_name>
```

Run synchronously — do **not** background this command. Step 4 reads
the completed review file; backgrounding produces stale results.

Pass the resolved branch name via `--branch` so the `pr` wrapper can
route it to context resolution. `pr review` handles bare repos, worktree
resolution, and fresh-vs-existing review detection internally.

`--push` publishes the fix commit. Without it the commit is still made and
the push is only drafted to stderr, which leaves the branch behind its own
review — the findings read as addressed while the remote still has the code
they were written about. In self-review mode the push is the only outward
write the flag enables: the publishing gate also covers replies and tracking
issues, but those belong to `pr comments`, and nothing on this path creates
them.

Budget for a long run. The push runs the repo's full pre-push gate — in
otto-workbench that is a gitleaks secret scan, `validate-all`,
`check-surface-compat`, tool-context regeneration, shellcheck, YAML/ZSH/JSON
checks, the selected bats files, and pytest, in that order — so the command
does not return when the review does. Give it a timeout that covers review
plus the full gate rather than letting a tool default kill it partway, which
leaves the fixes committed and unpushed and needs a bare `git push` to
finish.

**Exit 4 — the branch may already be superseded, and nothing was reviewed.**
Parse the JSON:

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

`kind` names the check that fired: `readds_removed_symbol` (the branch adds a
definition the default branch has deleted), `superseding_pr` (a merged PR
mentions that definition), or `rebase_skew` (the branch was replayed onto a base
that moved — reported for context, never the reason for the refusal).

Report every `detail` and stop. Do not re-run with the override on your own
judgment: reviewing a branch that re-adds deleted code produces findings about
code that should not exist, and they read as ordinary review comments. Read the
merged PR the `superseding_pr` signal names, present it, and let the user decide.
If they confirm the branch is still wanted, re-run with the flag in `override`:

```bash
pr review --self --fix --push --force --branch <branch_name>
```

### Step 4: Report results

Fixes are automatically committed by `pr review` — no manual commit needed.

Read the review file **after the command completes** and present:
- The commit message includes a per-finding summary (fixed findings with
  descriptions, skipped findings with reasons)
- If Must-fix or Should-fix findings remain unfixed, list them with any
  skip reasons annotated inline as `*(skipped — reason)*`

**Do not** ask "how would you like to proceed" or offer choices.
**Do not** attempt to fix remaining findings manually via Edit tool —
all fixing is done by `pr review --self --fix --push`. The fix agent determines
what is auto-fixable; trust its judgment.

Confirm the push landed rather than assuming it did — a drafted push prints
`DRAFT (not published)`, a gate failure fails the push without touching the
commit, and a push git reports as successful can still leave `HEAD` and
`@{u}` diverged if the remote didn't actually hold the commit or couldn't be
asked to confirm it. The check below can't tell those apart, but the fix is
the same either way — push again:

```bash
git rev-parse HEAD; git rev-parse @{u}
```

If they differ, say so and push the branch; do not report the work as shipped.

---

## Safety

- **Auto-committed and pushed.** Applied fixes are committed automatically after
  the fix pass completes, and `--push` sends the commit to the branch. The commit
  message includes fix/skip counts. Drop `--push` to commit locally and leave the
  branch unpublished.
- **Non-destructive.** All fixes are applied via Edit tool — individual changes
  are reviewable in the git log.
- **Idempotent.** Running twice on the same review skips already-fixed findings.
- **Review preserved.** The review file is kept in `~/.local/state/workbench/reviews/`
  for retro analysis — it is not deleted after fixing.
- **Superseded branches are refused, not reviewed.** A branch that re-adds code
  the default branch has deleted exits 4 before the first agent call. See Step 3.
