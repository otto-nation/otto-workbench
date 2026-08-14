# fixture

A stand-in worktree for the pr-rebase eval. This directory exists because
`eval-models` skips a case without a `src/` directory. The scoring itself never
reads it — the harness grades the command trace in `trace.jsonl`, not repo
contents — but a compliant session can run real `git` commands here (the `git`
shim is passthrough), so `go.mod` and `go.sum` are present to keep what `git
status`/`git log` shows consistent with what `responses.json`'s stubbed
`pr rebase --fix` output narrates having resolved.
