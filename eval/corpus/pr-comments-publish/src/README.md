# fixture

A stand-in worktree for the pr-comments eval. `eval-models` skips a case
without a `src/` directory, and this case's contents are committed as the
commit the stubbed fix report cites through `@@HEAD_SHORT@@`.

Unlike the other two `pr-comments` cases, this one grades publishing, so a
session has reason to resolve that sha and read what it holds. `deploy.sh` is
therefore the file the report's two fixed threads name, already in the state
they claim — a session that checks finds the drafts corroborated instead of
contradicted.
