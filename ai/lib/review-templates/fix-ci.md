Fix CI failures for branch ${branch_name} in ${repo}.

## Failures to fix

${tracking_content}

## Task

${answer_format}

## What earns each box

- **fixed** — a lint error with a clear fix, a test assertion over an obvious
  code bug, a build config issue, a missing import
- **declined** — the failure is flaky, it is an infrastructure fault rather
  than a code one, or the check itself is wrong
- **needs a person** — a design decision, an architectural change, or something
  needing input you do not have

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
