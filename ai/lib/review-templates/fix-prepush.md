Fix pre-push check failures on branch ${branch_name} in ${repo}.

An automated rebase replayed this branch and resolved its conflicts. The
pre-push checks then failed on the files below. Repair what the resolution left
behind.

## Files the check complained about

${tracking_content}

## The check output

${check_output}

## Task

${answer_format}

## What earns each box

- **fixed** — a formatting, build, lint, or import error the check named, or a
  conflict resolution that merged two versions into something that does not
  compile
- **declined** — the check output does not implicate this file, or the file is
  correct as it stands
- **needs a person** — the resolution dropped or duplicated logic and choosing
  what the branch meant is a judgement call, not a repair

## Rules

- The check output above is the oracle — fix what it reports, not what you would
  otherwise change about the file
- Make the minimal correct change — do not refactor surrounding code
- A conflict resolution that took one side wrongly is the likeliest cause; read
  the surrounding code before assuming the check is wrong about a file
- If a repair needs a design choice, tick `needs a person` and say what the
  choice is — leaving it unticked reports it as unread
- Do not add comments explaining the change
- Do not amend, revert, or rebase anything — the branch is mid-recovery and its
  commits are not yours to move. Edit the files and leave the committing alone

## Generated files

${generated_block}

## Tracking file location
${tracking_file}

## Worktree
${worktree_block}

## Turn budget
You have ${max_turns} turns. Process files systematically — batch independent file reads into single turns.
