# pr batch report `r1`

status: interrupted

## o/r#1

state: awaiting_decision · severity: block
branch: feat-base
findings:
- conflict_handwritten
  - files: a.go

- `pr rebase --fix --no-push --repo-dir /no/such/repo --branch feat-base` — resolve hand-written conflicts

## o/r#3

state: done · severity: block
branch: feat-moved
findings:
- closeout_owed: closeout owed per pr state
- push_refused: origin/feat-moved is bbbbbbb; the batch planned from aaaaaaa

- `pr comments --finish --post --repo-dir /no/such/repo --branch feat-moved` — closeout owed per pr state

## o/r#2

state: awaiting_decision · severity: act
branch: feat-child
findings:
- threads_need_human

- `pr comments --fix --repo-dir /no/such/repo --branch feat-child` — address review comments
