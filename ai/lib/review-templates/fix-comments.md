Fix PR review comment suggestions for branch ${branch_name} in ${repo}.

## Comment threads to address

${threads_content}

## Task

For each unchecked thread (`- [ ]`) above:

1. Read the referenced file at the specified line
2. Determine if the suggestion is auto-fixable:
   - **Fixable**: clear code change — rename, use existing helper, add guard, fix import, add nil check, remove dead code
   - **Not fixable**: requires design decision, architectural change, or user input
3. If fixable: apply the fix using the Edit tool on the source file
4. After fixing: update the thread checkbox from `- [ ]` to `- [x]` in the tracking file using Edit

## Priority order

Process threads in this order to maximize fixes within the turn budget:

1. File removals and dead code deletion
2. Import fixes, renames, and nil/guard additions
3. Changes using existing helpers or patterns
4. Everything else

## Rules

- For each fix, make the minimal correct change — do not refactor surrounding code
- If a suggestion references a function, type, or API — verify it exists in the codebase before using it
- If a suggestion is ambiguous or requires a design choice, skip it (leave unchecked)
- Do not add comments explaining the change — the reviewer already knows what they asked for
- When a "PR diff for this file" section is included, use it to understand what the PR changed before applying the fix
- Never run `gh` or any other command that writes to GitHub — posting is not your job and the tool will refuse the call. Everything you produce is delivered later, once the operator publishes

## PR description

A comment is sometimes answered by rewriting the PR description rather than the
code — a missing rationale, a wrong section, a claim the diff no longer supports.
To answer one that way:

1. Write the **complete** new description to `${pr_body_file}` — it replaces the
   existing one wholesale, so include every section you want to keep
2. Check the thread's box like any other fix

Leave the file absent when no comment calls for a description change.

## Tracking file location
${tracking_file}

## Worktree
${worktree_block}

## Turn budget
You have ${max_turns} turns. Process threads systematically — batch independent file reads into single turns.
