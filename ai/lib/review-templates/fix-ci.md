Fix CI failures for branch ${branch_name} in ${repo}.

## Your role

${role_block}

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

## What the `fixed` box asks for

Here the failing check is the test, so the box asks for the check rather than
for a new one: name the check you re-ran and what it said. "golangci-lint run:
clean" or "pytest tests/foo_test.py: 12 passed" is the answer.

The one case that owes a *new* test is a failure whose root cause was a code
bug the suite did not catch — the check caught it downstream, and nothing
would catch it again. Add the case that fails without your fix and name it.
A failure whose cause was the check's own fixture, a lint rule, or a build
config owes nothing beyond the re-run.

## Rules

- Make the minimal correct change — do not refactor surrounding code
- For lint errors, fix the specific issue flagged — do not "improve" surrounding code
- For test failures, determine whether the test or the code is wrong before fixing
- If a failure is ambiguous or requires a design choice, tick `needs a person` and say what the choice is — leaving it unticked reports it as unread
- Do not add comments explaining the change

## Generated files

${generated_block}

## Tracking file location
${tracking_file}

## Worktree
${worktree_block}

## Turn budget
You have ${max_turns} turns. Process failures systematically — batch independent file reads into single turns.
