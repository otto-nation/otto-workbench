---
name: pr-comments
description: "Analyze and address PR review comments with lifecycle tracking: fetch, classify, verify, fix, reply, and resolve across multi-round review cycles."
source: otto-workbench/ai/claude/skills/pr-comments/SKILL.md
invocation: "/pr-comments [<pr_number>]"
---

# PR Comments

Responds to review comments on a PR with full lifecycle tracking. Fetches threads, classifies them, verifies suggestions against the codebase, applies fixes, replies inline, and auto-resolves acknowledged threads.

Tracks multi-round review cycles: knows what's been addressed, what the reviewer acknowledged, what they pushed back on, and what's still open.

Run with `/pr-comments` or `/pr-comments <pr_number>`.

---

## Steps

### 1. Fetch status and display dashboard

Run the status script to fetch all threads, compute lifecycle states, and display the dashboard:

```bash
claude-review threads [--pr <number>] [--repo <owner/repo>] [--repo-dir <path>]
```

The script outputs:
- **stderr:** Human-readable status dashboard (reviewer verdicts, thread counts by state, merge blockers)
- **stdout:** Structured JSON report with all threads, their lifecycle states, comments, and verdicts

**Bare repo handling:** If CWD is a bare repo (`git rev-parse --is-bare-repository` returns `true`), the script cannot auto-detect the git toplevel. Use `wt switch <branch> --no-cd --format json --no-hooks` to find the worktree path, then pass it via `--repo-dir`:

```bash
wt switch <branch> --no-cd --format json --no-hooks 2>/dev/null
# Extract .path from JSON output, then:
claude-review threads --pr <number> --repo <owner/repo> --repo-dir <worktree_path> 2>&1
```

**Invocation rules:** Run the command as a single simple statement. Capture both stderr and stdout together with `2>&1`. The dashboard text appears first, followed by the JSON report starting with `{` on its own line — parse from there. Never use temp files, `<<<`, compound multi-statement commands, or run the script twice.

If the script fails (non-zero exit code), ask the user which PR to address and re-run with explicit `--pr` and `--repo` flags.

Parse the JSON report from stdout. If there are no threads in `new`, `contested`, or `ambiguous` state, and no unaddressed issue-level comments, report that nothing needs action and stop.

If a specific reviewer was mentioned (e.g., "address Gemini's comments"), filter the threads to that reviewer.

### 2. Classify threads needing action

From the JSON report, present threads that need action, grouped in priority order:

1. **Contested** — reviewer pushed back after your fix (urgent)
2. **Ambiguous** — script couldn't determine if the reply was acknowledgment or pushback (needs your input)
3. **New** — never addressed

For each thread, show the **full conversation** (all comments in the thread), not just the latest comment. Include the lifecycle state and reviewer name.

Classify each as:

| Classification | Description | Action |
|---|---|---|
| **Actionable suggestion** | Specific code change request | Verify and apply |
| **Question** | Asks for clarification or explanation | Answer |
| **Approval/acknowledgment** | No action needed | Skip |
| **Conflicting** | Contradicts another reviewer's suggestion | Flag |

For **contested** threads, also show what changed since the last round — your prior reply and the reviewer's response.

Present the classification table for user confirmation:

```
## Threads Needing Action

| # | State | Reviewer | File | Classification | Summary |
|---|-------|----------|------|----------------|---------|
| 1 | ⚠ contested | @alice | handler.go:42 | Suggestion | Still wants RunTx, not manual tx |
| 2 | ? ambiguous | @bob | service.go:18 | Question | Unclear if ack or pushback |
| 3 | → new | @gemini | util.go:5 | Suggestion | Use shared helper |

Proceed with this classification? (y/n)
```

Also classify any **issue-level comments** from the `issue_comments` array in the JSON report. These are general PR discussion comments (not inline code threads). They don't have lifecycle states — classify them the same way as threads (suggestion, question, acknowledgment, conflicting) and include them in the classification table.

### 3. Verify actionable suggestions

For each suggestion classified as actionable:

1. Read the file and line referenced in the comment
2. Determine if the suggestion is valid against the current code state
3. If the suggestion references a function, utility, or API — search the codebase to confirm it exists and has the signature the reviewer claims

For 3 or more suggestions, launch sub-agents in parallel for verification. Each agent should:

> A PR reviewer suggested changing `X` to `Y` in `path/to/file` at line N.
> Verify: does `Y` actually exist in the codebase? Check the relevant packages.
> Report: is the suggestion correct? What is the actual name/signature? Under 100 words.

Mark each suggestion as:
- **valid** — suggestion is correct, apply it
- **invalid** — suggestion is incorrect (with specific reason: "utility does not exist", "wrong signature", "would break callers at X:N")
- **needs-discussion** — ambiguous or requires design input

### 4. Apply valid fixes

For each `valid` suggestion:
1. Edit the file to address the comment
2. After all edits are applied, stage and commit:

```bash
git add <changed-files>
git commit -m "fix: address review feedback on PR #<number>"
git push
```

Group all fixes into a single commit. Do not create separate commits per comment.

### 5. Reply to each comment

Reply inline to every addressed comment. Commit and push before replying so replies reference the fix.

**IMPORTANT:** The PR number is required in the URL path — `pulls/{number}/comments/...`, not `pulls/comments/...`.

**Never use `-f body="..."`** — backticks and special characters cause shell escaping failures. Use `-F body=@-` with a quoted heredoc:

```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -F body=@- <<'REPLY_BODY'
<reply text — backticks, quotes, markdown all safe here>
REPLY_BODY
```

Use the `databaseId` from the thread's first comment as the `comment_id` for replies.

Reply examples by outcome:

**Accepted:**
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -F body=@- <<'REPLY_BODY'
Fixed.
REPLY_BODY
```

**Rejected (with evidence):**
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -F body=@- <<'REPLY_BODY'
Not applying — `helperFn()` does not exist in the `utils` package. Checked `pkg/utils/` and `internal/utils/`.
REPLY_BODY
```

**Re-addressing a contested thread:**
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -F body=@- <<'REPLY_BODY'
Good point about the error handling. Updated to use `RunTx` with proper rollback — see latest commit.
REPLY_BODY
```

**Question answered / Conflicting:** Same heredoc pattern.

### 6. Resolve verified threads

After replying, resolve threads that have been verified (reviewer acknowledged your fix):

```bash
claude-review threads --resolve-verified [--pr <number>] [--repo <owner/repo>]
```

This auto-resolves verified threads on GitHub via GraphQL mutation. Only threads where the reviewer explicitly acknowledged the fix are resolved — never contested or ambiguous threads.

### 7. Emit feedback signals

After processing all comments, print a structured summary:

```markdown
## Review Feedback Summary

| Thread | Reviewer | State | Outcome | Reason |
|--------|----------|-------|---------|--------|
| T_abc | @alice | contested→addressed | re-fixed | Updated to use RunTx |
| T_def | @gemini | new→addressed | accepted | Fixed in abc123 |
| T_ghi | @bob | new→addressed | rejected | Utility does not exist |
| T_jkl | @alice | verified→resolved | auto-resolved | Reviewer acknowledged |
```

This summary appears in the session transcript and is picked up by `/dream` for review quality tracking.

### 8. Report

Print:
- Number of fixes applied
- Number of replies posted
- Number of threads resolved
- Number of threads still contested or needing discussion
- Number of threads awaiting reviewer response
- Link to the PR

---

## Thread Lifecycle States

The status script tracks each thread through these states:

```
new → addressed → verified → resolved
                ↘ contested (→ re-addressed → verified → resolved)
```

| State | Meaning |
|-------|---------|
| **new** | Reviewer comment with no reply from you |
| **addressed** | You replied or pushed a fix, awaiting reviewer response |
| **verified** | Reviewer acknowledged your fix (short positive reply, thumbs up) |
| **contested** | Reviewer pushed back after your fix |
| **ambiguous** | Reply couldn't be classified as ack or pushback — needs your input |
| **resolved** | Thread resolved on GitHub |

---

## Constraints

- NEVER apply fixes without user confirmation of the classification (Step 2)
- NEVER reply to comments without user confirmation
- NEVER auto-resolve contested or ambiguous threads — only verified ones
- Handle bot reviewers (Gemini, CodeRabbit, etc.) the same as humans — verify all suggestions against source code
- If conflicting suggestions exist, flag both and apply neither until resolved
- If the PR has been updated since comments were posted, warn the user before applying fixes
- State file lives at `<worktree>/ignore/pr-comments/state.json` — travels with the branch
