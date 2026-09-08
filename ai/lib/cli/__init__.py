"""Layer 8 — command entry points. May import: core, config, git, gh, agent, pr, fix, rebase, review, eval, retro.

The top of the stack, and the only layer allowed to see all of it. A binary
under `ai/bin/` is a shim over one module here: the argument parser, the
`main(argv) -> int`, and the flow that calls the library. Nothing imports `cli`,
which is what keeps an entry point from becoming somewhere to park a helper —
a module here has no callers to answer to, so its upward edges are never
checked.
"""
