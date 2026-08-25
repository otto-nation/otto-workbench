Fix PR review comment suggestions for branch ${branch_name} in ${repo}.

## Comment threads to address

${tracking_content}

## Task

Every thread above carries three boxes. Answer each thread by ticking exactly
one of them with the Edit tool, in the tracking file:

1. Read the referenced file at the specified line
2. Decide which answer the thread has earned:
   - `- [x] fixed` — you applied the change. Clear code changes are what belongs
     here: rename, use existing helper, add guard, fix import, add nil check,
     remove dead code. Apply it with the Edit tool on the source file first
   - `- [x] declined — <why>` — you read the comment and it should not be acted
     on: the premise does not hold, the code already does what it asks, or the
     change would be wrong. Replace `<why>` with the reason, in one sentence
   - `- [x] needs a person — <why>` — the comment is valid but the call is not
     yours: a design decision, an architectural change, or something needing
     input you do not have. Replace `<why>` with what the decision turns on
3. Leave all three boxes unticked only for a thread you never got to. That reads
   as work still owed and the thread is handed to another pass, so use it for
   what you ran out of turns for — never as a way to pass over a thread you
   read and had an answer for

## Priority order

Process threads in this order to maximize fixes within the turn budget:

1. File removals and dead code deletion
2. Import fixes, renames, and nil/guard additions
3. Changes using existing helpers or patterns
4. Everything else

## Rules

- For each fix, make the minimal correct change — do not refactor surrounding code
- If a suggestion references a function, type, or API — verify it exists in the codebase before using it
- If a suggestion is ambiguous or requires a design choice, tick `needs a person` and say what the choice is — leaving it unticked reports it as unread
- Do not add comments explaining the change — the reviewer already knows what they asked for
- When a "PR diff for this file" section is included, use it to understand what the PR changed before applying the fix
- Never run `gh` or any other command that writes to GitHub — posting is not your job and the tool will refuse the call. Everything you produce is delivered later, once the operator publishes

## PR description

A comment is sometimes answered by rewriting the PR description rather than the
code — a missing rationale, a wrong section, a claim the diff no longer supports.
To answer one that way:

1. Write the **complete** new description to `${pr_body_file}` — it replaces the
   existing one wholesale, so include every section you want to keep
2. Tick the thread's `fixed` box like any other fix

Leave the file absent when no comment calls for a description change.

## Tracking file location
${tracking_file}

## Worktree
${worktree_block}

## Turn budget
You have ${max_turns} turns. Process threads systematically — batch independent file reads into single turns.
${main_worktree}
