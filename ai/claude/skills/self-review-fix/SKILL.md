---
name: self-review-fix
description: "Run self-review and auto-fix findings. Wraps pr review --self --fix. Can also fix from an existing review without re-running. TRIGGER when: user asks to self-review a branch, run pre-merge review, or auto-fix findings before PR creation. SKIP: reviewing someone else's PR (use code-review or review); addressing existing PR review comments (use pr-comments)."
source: otto-workbench/ai/claude/skills/self-review-fix/SKILL.md
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
   `~/.config/workbench/reviews/`
3. If no review, or the review is stale, run `pr review --self --fix`
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
   ls ~/.config/workbench/reviews/<repo>-self-<sanitized>/review.md
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
pr review --self --fix --branch <branch_name>
```

Run synchronously — do **not** background this command. Step 4 reads
the completed review file; backgrounding produces stale results.

Pass the resolved branch name via `--branch` so the `pr` wrapper can
route it to context resolution. `pr review` handles bare repos, worktree
resolution, and fresh-vs-existing review detection internally.

### Step 4: Report results

Fixes are automatically committed by `pr review` — no manual commit needed.

Read the review file **after the command completes** and present:
- The commit message includes a per-finding summary (fixed findings with
  descriptions, skipped findings with reasons)
- If Must-fix or Should-fix findings remain unfixed, list them with any
  skip reasons annotated inline as `*(skipped — reason)*`

**Do not** ask "how would you like to proceed" or offer choices.
**Do not** attempt to fix remaining findings manually via Edit tool —
all fixing is done by `pr review --self --fix`. The fix agent determines
what is auto-fixable; trust its judgment.

---

## Safety

- **Auto-committed.** Applied fixes are committed automatically after the fix
  pass completes. The commit message includes fix/skip counts.
- **Non-destructive.** All fixes are applied via Edit tool — individual changes
  are reviewable in the git log.
- **Idempotent.** Running twice on the same review skips already-fixed findings.
- **Review preserved.** The review file is kept in `~/.config/workbench/reviews/`
  for retro analysis — it is not deleted after fixing.
