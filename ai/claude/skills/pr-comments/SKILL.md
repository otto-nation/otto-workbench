---
name: pr-comments
description: "Analyze and address PR review comments with lifecycle tracking: fetch, classify, verify, fix, then draft replies for approval before publishing with --post. TRIGGER when: user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments. SKIP: initial code review requests (use code-review or pr review instead); self-review before PR creation (use self-review-fix instead)."
source: otto-workbench/ai/claude/skills/pr-comments/SKILL.md
invocation: "/pr-comments [<pr_number_or_branch>]"
trigger: "Use when user asks about PR comments, review comments, reviewer feedback, or addressing suggestions on a PR; user references a PR with review threads; user asks to analyze, fix, respond to, or resolve review comments."
skip: "Do not use for initial code review requests (use code-review or pr review instead); do not use for self-review before PR creation (use self-review-fix instead)."
---

# PR Comments

Triages and fixes PR review comments. Wraps `pr comments --fix`, which classifies
threads via AI, applies mechanical fixes, and outputs structured JSON for any
threads that need human input.

Nothing reaches GitHub until the user approves it. Replies, the summary comment,
thread resolutions, and deferral issues are drafted to stderr; `--post` is what
publishes them, and it is only ever passed after the user has read the drafts.

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
| `comment_items` | Decomposed items from top-level comments (see below) |
| `issue_comments` | Raw issue-level discussion comments (for fallback when items aren't available) |
| `review_body_comments` | Raw review-level body comments (for fallback when items aren't available) |

The `fix_pass` object contains:

| Field | Contents |
|-------|----------|
| `fixed` | Threads and items the agent auto-fixed (committed + pushed) |
| `needs_human` | Threads and items requiring user input (contested, conflicting, questions, needs_discussion) |
| `dismissed` | Threads and items dismissed because the reviewer's premise was factually wrong |
| `already_addressed` | Threads and items the code already satisfies — agreement with the reviewer, not rejection |
| `deferred` | Threads the agent could not auto-fix in the current pass |
| `commit_sha` | Short SHA of the fix commit, or null |
| `replies_posted` | Count of per-thread replies posted to GitHub |
| `summary_url` | URL of the summary issue comment, or null |
| `summary_deferred` | `true` when summary was deferred because `needs_human` threads exist |
| `comment_items` | Breakdown of comment item outcomes: `{fixed, needs_human, dismissed, deferred}` |

**Comment items** (`comment_items` array at the top level): when top-level PR
comments (issue comments or review body comments) contain multiple actionable
points, the triage step decomposes them into individual items. Each item has:

| Field | Contents |
|-------|----------|
| `id` | Synthetic ID (`ic-{comment_id}-{index}` or `rb-{review_id}-{index}`) |
| `source_id` | Original comment ID |
| `source_type` | `"issue_comment"` or `"review_body"` |
| `classification` | Same as threads: `actionable_suggestion`, `question`, `approval`, `conflicting` |
| `verification` | For actionable items: `valid`, `already_addressed`, `invalid`, `needs_discussion` |
| `summary` | One-line summary of the specific item |
| `file` | File path if referenced (empty string if not) |
| `line` | Line number if referenced (0 if not) |
| `reviewer` | Comment author |

Present items the same way as threads — they flow through the same
fix/needs_human/dismissed pipeline. Items with synthetic IDs (prefixed `ic-`
or `rb-`) are comment items; regular thread IDs are inline review threads.

**Report auto-fixes:** "Fixed N threads/items (commit SHA). M need your input. K skipped."

**If `needs_human` and `deferred` are both empty and no unseen comments:**
done — no further action needed.

**If `needs_human` is non-empty:** present each with its reason and summary.
Ask the user what to do for each:
- **Fix it** — apply the edit inline, then commit and push
- **Skip** — move on
- **Reply** — compose a reply to the reviewer

**If `deferred` is non-empty:** present each the same way, with the same
options plus **Track it** — file the thread on the deferred tracking issue by
passing its id to `--track` in Step 4. A deferred thread is one the agent
attempted twice and could not land, not one anyone decided to postpone; filing
it away is a decision the user makes per thread, never a default. Record which
ids the user chooses here — `--finish` files nothing without them.

When investigating `needs_human` or `deferred` threads, use the main worktree
as a read-only reference for code outside the PR diff — imports, callers,
existing patterns, or shared utilities. The script updates the main
worktree to `origin/main` before the fix pass, so it reflects the
current baseline. Find it via `wt switch main --no-cd --format json --no-hooks`.

**Do not** attempt to fix `dismissed` threads — the agent already determined
the reviewer's premise was factually wrong.

**Fallback for raw comments**: if `comment_items` is empty but unseen
`issue_comments` or `review_body_comments` exist (e.g., when running without
`--fix`/`--triage`), present unseen ones with the author and a summary as before.

### Step 4: Present drafts and get approval

The fix pass drafts its per-thread replies and summary to stderr, prefixed
`DRAFT (not published)`. Show them to the user — grouped by thread, with the
reviewer and the claim each reply makes — and ask whether to publish.

Every factual claim in a reply must hold against the current code. Check the
ones that assert absence ("nothing calls X", "this is unused") before showing
them; an incorrect claim posted to a reviewer has to be retracted publicly.

Only after the user approves, and only once Step 3 is complete:

```bash
pr comments --finish --post [--track <thread_id> ...] [--repo-dir <PATH>]
```

That sends the drafted replies (including those whose commit had not yet been
pushed), posts the summary, files the tracking issue for the threads named by
`--track`, and resolves verified threads. The summary is meant to describe
a finished conversation, so don't publish before the discussion is done. A
drafted run recorded nothing as posted, so the queue is intact — no need to
re-run `--fix`.

Pass one `--track <thread_id>` per thread the user chose to track in Step 3.
Omit the flag entirely when they chose none — a bare `--finish` files nothing
and lists the unfiled ids. Use `--track-all` only when the user has reviewed the
whole deferred set and asked for all of it. Never infer `--track` from the fact
that a thread was deferred: the reply it posts says a reviewer's finding was
triaged and postponed, under the PR author's name.

If the user wants changes to a reply first, edit and post it manually (below),
then run `--finish --post` for the rest.

For manual replies to `needs_human` threads, write the body to a file and post it
with `--reply`:

```bash
pr comments --reply <thread_or_comment_id> --body-file <PATH> [--repo-dir <PATH>]
```

`--reply` takes the thread's node ID, any comment `databaseId` in it, or a
`...#discussion_r<id>` URL. It edits our standing reply when that reply is still
the last comment on the thread, and posts a new one only once a reviewer has
answered — so a revised position replaces the old one instead of stacking under
it. Do **not** call `gh api .../replies` directly: that path has no dedup, and it
is what leaves a thread holding several of our comments that disagree.

Print summary: fixes applied, replies posted, threads resolved, threads still open.

---

## Constraints

- Never pass `--post` before the user has seen the drafts and approved them
- Never apply fixes without user confirmation for `needs_human` items
- One reply per thread. If our position changed, revise the existing reply via
  `--reply` — a second comment leaves the reviewer holding two answers and no
  way to tell which one stands
- Back every factual claim in a reply with a blob permalink pinned to a SHA
  (`https://github.com/<owner>/<repo>/blob/<sha>/<path>#L<line>`), never a
  branch-relative URL, which drifts as the branch moves. This applies hardest to
  disagreement: if you cannot point at the line that settles it, say what you
  checked and ask
- Never file a `deferred` thread on the tracking issue without the user
  choosing to — deferral is a decision, not a fallback for a failed fix pass.
  Pass their chosen ids to `--track`; `--track-all` is the one blanket form,
  and only after they have reviewed the whole set
- Never auto-resolve contested or ambiguous threads — only verified ones
- Handle bot reviewers (Gemini, CodeRabbit, etc.) the same as humans
- If conflicting suggestions exist, flag both and apply neither until resolved
