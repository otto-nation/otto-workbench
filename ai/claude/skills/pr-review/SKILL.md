---
name: pr-review
description: "Post or update a GitHub PR review from /tmp/reviews/. Creates PENDING reviews with inline comments, and can analyze and respond to existing review threads."
---

# PR Review

Manages the lifecycle of GitHub PR reviews: post new reviews, update existing ones, and respond to review threads.

Run with `/pr-review <pr_number>`.

---

## Steps

### 1. Check for existing review

```bash
repo=$(basename $(git rev-parse --show-toplevel))

# Check for an existing PENDING review by the current user
gh api repos/{owner}/{repo}/pulls/<pr_number>/reviews \
  --jq '[.[] | select(.user.login == "<current_user>" and .state == "PENDING")] | first'
```

Also check for submitted reviews with unanswered comment threads:

```bash
# Get all review comments and their reply threads
gh api repos/{owner}/{repo}/pulls/<pr_number>/comments \
  --jq '[.[] | select(.in_reply_to_id == null)] | map({id, path, body, user: .user.login, updated_at, replies: []})'
```

**If an existing review has unanswered responses:**
- Show the user a summary: how many threads, which files, who responded
- Ask: "There are N unanswered threads on this PR. Would you like to analyze them and update the review file?"
- If yes, proceed to Step 1b
- If no, skip to Step 2

### 1b. Analyze unanswered threads (on request)

For each unanswered thread:
1. Read the original comment and all replies
2. Determine if the response resolves the finding, requests clarification, or disagrees
3. Update the review file in `/tmp/reviews/<repo>-<pr_number>.md`:
   - Mark resolved findings with `~~strikethrough~~` and note the resolution
   - Add new findings surfaced by the discussion
   - Append an `## Open Threads` section with threads needing a response
4. Present the updates to the user for confirmation before writing

### 2. Load the review file

Read `/tmp/reviews/<repo>-<pr_number>.md`. If the file doesn't exist, stop and tell the user to run the reviewer agent first.

Parse the file to extract:
- Summary (from the `## Summary` section)
- Findings with file paths, line numbers, severity tags, and descriptions
- Verdict
- Skip any `~~strikethrough~~` findings (resolved)

### 3. Fetch the latest PR state

```bash
# Get the latest commit SHA on the PR
gh pr view <pr_number> --json headRefOid --jq '.headRefOid'

# Get the diff to validate file paths and line positions
gh api repos/{owner}/{repo}/pulls/<pr_number> \
  --header 'Accept: application/vnd.github.v3.diff'
```

If the PR has been updated since the review file was written (compare the `<!-- date: -->` in the review file against the latest commit date), warn the user and ask whether to proceed.

### 4. Validate findings against the diff

For each finding in the review file:

1. **Verify the file exists in the diff.** If a file path from the review is not in the diff, skip it and warn the user.

2. **Map line numbers to diff positions.** The GitHub API requires `line` (the line number in the file at the HEAD of the PR branch) not a raw diff hunk position. For each finding:
   - Parse the diff hunks for the target file
   - Confirm the referenced line number falls within a changed hunk (added or context line)
   - If the line is not in the diff, convert to a file-level comment (no `line` field) and note this in the output

3. **Report skipped findings.** Print any findings that couldn't be mapped so the user can address them manually.

### 5. Post or update the review

**If no existing PENDING review:**

```bash
gh api repos/{owner}/{repo}/pulls/<pr_number>/reviews \
  --method POST \
  -f commit_id="<head_sha>" \
  -f event="PENDING" \
  -f body="<summary + verdict>" \
  -f 'comments[][path]=<file>' \
  -f 'comments[][line]=<line_in_file>' \
  -f 'comments[][side]=RIGHT' \
  -f 'comments[][body]=<severity_tag + finding>'
```

**If updating an existing PENDING review:** add new comments to the existing review and skip findings already posted.

**If replying to threads from Step 1b:** post replies to the appropriate comment threads:

```bash
gh api repos/{owner}/{repo}/pulls/<pr_number>/comments/<comment_id>/replies \
  --method POST \
  -f body="<response>"
```

Format each comment body as:
```
**[must-fix]** <finding description>
```

### 6. Report

Print:
- Number of inline comments posted (new + replies)
- Number of findings skipped (with reasons)
- Number of threads responded to
- Link to the PR
- Reminder: review is PENDING — verify and submit from the GitHub UI

---

## Constraints

- NEVER submit the review (`APPROVE`, `REQUEST_CHANGES`, `COMMENT`) — only create as `PENDING`
- NEVER modify the review file without user confirmation
- If the PR has been updated since the review file was written, warn and ask before proceeding
- If no findings can be mapped to the diff, stop and explain why rather than posting an empty review
