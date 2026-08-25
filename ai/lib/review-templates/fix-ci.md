Fix CI failures for branch ${branch_name} in ${repo}.

## Failures to fix

${tracking_content}

## Task

Every failure above carries three boxes. Answer each failure by ticking exactly
one of them with the Edit tool, in the tracking file:

1. Read the referenced file at the specified line
2. Decide which answer the failure has earned:
   - `- [x] fixed` — you applied the change. A lint error with a clear fix, a
     test assertion over an obvious code bug, a build config issue or a missing
     import belongs here. Apply it with the Edit tool on the source file first
   - `- [x] declined — <why>` — you read the failure and it should not be acted
     on: it is flaky, it is an infrastructure fault rather than a code one, or
     the check itself is wrong. Replace `<why>` with the reason, in one sentence
   - `- [x] needs a person — <why>` — the failure is real but the call is not
     yours: a design decision, an architectural change, or something needing
     input you do not have. Replace `<why>` with what the decision turns on
3. Leave all three boxes unticked only for a failure you never got to. That
   reads as work still owed and the failure is handed to another pass, so use it
   for what you ran out of turns for — never as a way to pass over a failure you
   read and had an answer for

## Rules

- Make the minimal correct change — do not refactor surrounding code
- For lint errors, fix the specific issue flagged — do not "improve" surrounding code
- For test failures, determine whether the test or the code is wrong before fixing
- If a failure is ambiguous or requires a design choice, tick `needs a person` and say what the choice is — leaving it unticked reports it as unread
- Do not add comments explaining the change

## Tracking file location
${tracking_file}

## Worktree
${worktree_block}

## Turn budget
You have ${max_turns} turns. Process failures systematically — batch independent file reads into single turns.
