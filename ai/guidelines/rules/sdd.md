# SDD — Artifact Paths

- All SDD artifacts (progress ledger, task briefs, task reports, review packages) go in `ignore/sdd/` under the worktree root — never use `git rev-parse --git-path sdd`, which resolves inside `.git/` and triggers sensitive-file permission prompts on every write
- Create the directory with `mkdir -p ignore/sdd` before writing any SDD file
- Progress ledger: `ignore/sdd/progress.md`
- Task briefs: pass an explicit OUTFILE to `task-brief` — e.g. `task-brief PLAN N ignore/sdd/task-N-brief.md`
- Task reports: `ignore/sdd/task-N-report.md` (derived from the brief path, same directory)
- Review packages: pass an explicit OUTFILE to `review-package` — e.g. `review-package BASE HEAD ignore/sdd/review-BASE..HEAD.diff`
