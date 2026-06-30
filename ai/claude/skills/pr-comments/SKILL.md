---
name: pr-comments
description: "Analyze and address PR review comments with lifecycle tracking: fetch, classify, verify, fix, reply, and resolve across multi-round review cycles. TRIGGER when: user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments. SKIP: initial code review requests (use code-review or pr review instead); self-review before PR creation (use self-review-fix instead)."
source: otto-workbench/ai/claude/skills/pr-comments/SKILL.md
invocation: "/pr-comments [<pr_number_or_branch>]"
trigger: "Use when user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments."
skip: "Do not use for initial code review requests (use code-review or pr review instead); do not use for self-review before PR creation (use self-review-fix instead)."
---

# PR Comments

Triages and fixes PR review comments. Wraps `pr comments --fix`, which classifies
threads via AI, applies mechanical fixes, and outputs structured JSON for any
threads that need human input.

Run with `/pr-comments`, `/pr-comments <pr_number>`, or `/pr-comments <branch_name>`.

---

## Arguments

- `pr_number_or_branch` (optional): PR number, URL, or branch name to address
  comments on. Defaults to auto-detection from the current branch.
  - Numeric values are treated as PR numbers
  - Values containing `/` are treated as branch names

---

## Steps

### Step 1: Resolve argument

Determine how to invoke the CLI. The `pr` script handles branch resolution
internally via `pr_context` — pass the argument through directly.

**If no argument provided** and CWD is inside the target repo worktree:
no resolution needed — `pr comments` auto-detects from CWD. Skip to Step 2.

**If a branch name argument was provided** (contains `/` or is not numeric):

Find the worktree path:
```bash
wt switch <argument> --no-cd --format json --no-hooks
```
Extract the `path` from the JSON output. This is the only value the CLI
needs — it derives repo, branch, and PR number from the worktree.

**If a PR number was provided** (numeric): pass it directly as `--pr`.

### Step 2: Run fix pass

Single command — pass only one identifier, never both `--pr` and `--repo-dir`:

```bash
pr comments --fix --repo-dir <PATH>
```

Or when CWD is inside the worktree:
```bash
pr comments --fix
```

Or when only a PR number is known (CWD must be inside the repo):
```bash
pr comments --fix --pr <NUMBER>
```

Run synchronously — do **not** background this command.

**Invocation rules:** Capture both stderr and stdout together with `2>&1`.
The dashboard and agent progress appear first (stderr), followed by the JSON
report starting with `{` on its own line — parse from there.

If the script fails (non-zero exit code), show the error and stop.

### Step 3: Report and handle results

Parse the JSON output. Top-level fields:

| Field | Contents |
|-------|----------|
| `fix_pass` | Object with fix results (see below) |
| `issue_comments` | Issue-level discussion comments from reviewers (not inline threads) |

The `fix_pass` object contains:

| Field | Contents |
|-------|----------|
| `fixed` | Threads the agent auto-fixed (committed + pushed) |
| `needs_human` | Threads requiring user input (contested, conflicting, questions, needs_discussion) |
| `skipped` | Threads the agent could not auto-fix |
| `commit_sha` | Short SHA of the fix commit, or null |
| `replies_posted` | Count of per-thread replies posted to GitHub |
| `summary_url` | URL of the summary issue comment, or null |

**Report auto-fixes:** "Fixed N threads (commit SHA). M threads need your input. K skipped."

**If `needs_human` is empty and no `issue_comments`:** done — no further action needed.

**If `needs_human` is non-empty:** present each with its reason and summary.
Ask the user what to do for each:
- **Fix it** — apply the edit inline, then commit and push
- **Skip** — move on
- **Reply** — compose a reply to the reviewer

**Do not** attempt to fix `skipped` threads — the agent already determined
they require judgment.

**Issue-level comments** (`issue_comments` array in the JSON output): these are
general discussion comments from reviewers (not inline review threads). Present
each with the author and a summary. These may require a reply or action — ask
the user.

### Step 4: Handle remaining threads and resolve

The script automatically posts per-thread replies (with summary and commit link)
and a summary issue comment after fixing and pushing. No manual reply posting needed.

For manual replies to `needs_human` threads, use the `databaseId` from the thread's first comment:

```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -F body=@- <<'REPLY_BODY'
<reply text>
REPLY_BODY
```

After all manual work is done, resolve verified threads:

```bash
pr comments --resolve [--repo-dir <PATH>]
```

Print summary: fixes applied, replies posted, threads resolved, threads still open.

---

## Constraints

- Never apply fixes without user confirmation for `needs_human` items
- Never auto-resolve contested or ambiguous threads — only verified ones
- Handle bot reviewers (Gemini, CodeRabbit, etc.) the same as humans
- If conflicting suggestions exist, flag both and apply neither until resolved
