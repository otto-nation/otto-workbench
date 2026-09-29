"""Entry points that end in each of the ways a real one can, for the seam.

`publishing.call_entry_point` resolves a `"<module>:<attr>"` string and
imports it, so a test of what it does with the result needs something real to
import — a `mock.patch` on `importlib.import_module` would be testing the
patch. These are the endings that actually occur across `ai/lib`: a returned
code, `sys.exit` with and without an argument, `sys.exit` with a message,
`SystemExit` raised directly, and an interrupt.

Not a test module. It is imported by name from `tests/pr_comments_test.py`
rather than collected, and nothing here asserts anything.
"""

import sys

from core import publishing


def returns_three(argv, **kwargs) -> int:
    return 3


def returns_none(argv, **kwargs):
    return None


def exits_zero(argv, **kwargs):
    # `review.preflight.check_stale_review` on a declined "Re-review anyway?".
    sys.exit(0)


def exits_four(argv, **kwargs):
    # `review.preflight.refuse_if_superseded` — supersession.EXIT_SUPERSEDED.
    sys.exit(4)


def exits_bare(argv, **kwargs):
    sys.exit()


def exits_with_a_message(argv, **kwargs):
    # argparse and a few library paths exit with a string rather than a code.
    sys.exit("could not read the review")


def interrupted(argv, **kwargs):
    raise KeyboardInterrupt


def echoes_argv(argv, **kwargs) -> int:
    return len(argv)


def requires_a_kwarg(argv, *, install_signal_handler) -> int:
    # `cli.claude_review.main` takes this, and an in-process caller must pass
    # False so it does not replace the handler the entry point installed.
    return 0 if install_signal_handler is False else 1


def opens_the_gate(argv, **kwargs) -> int:
    publishing.enable()
    return 0


def opens_the_gate_then_raises(argv, **kwargs) -> int:
    # Stands in for a bug in any real handler: `scope()` must restore the gate
    # on an uncaught exception, not just on a clean return or `sys.exit`.
    publishing.enable()
    raise RuntimeError("the run failed after opening the gate")


def holds_the_gate(argv, **kwargs) -> int:
    publishing.hold("a question this run could not answer")
    return 0


def reports_the_gate(argv, **kwargs) -> int:
    return 1 if publishing.enabled() else 0
