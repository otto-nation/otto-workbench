"""Layer 7 — eval harness. May import: core, config, git, gh, agent, pr, fix, review.

`import eval.floors` binds `eval`, shadowing the builtin for the rest of that
module. That is survivable rather than clever: no module under `ai/` calls the
builtin `eval`, and nothing should. If one ever needs to, it reaches it as
`builtins.eval` rather than this package being renamed — the package name is
the one the harness is called by everywhere else.
"""
