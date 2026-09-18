"""The lease a replayed branch is force-pushed under.

``git push --force-with-lease`` with no expected value protects the remote by
requiring it to match *our remote-tracking ref*. That is the wrong guarantee for
this tool, and git's own manual says so:

    A general note on safety: supplying this option without an expected value,
    i.e. as ``--force-with-lease`` or ``--force-with-lease=<refname>`` interacts
    very badly with anything that implicitly runs ``git fetch`` on the remote to
    be pushed to in the background.

A rebase run begins with exactly that fetch. So a colleague's commit, pushed
while we were not looking, is pulled into ``origin/<branch>`` by our own fetch,
the bare lease then finds the remote matching what it expects, and the replay
force-pushes their work away. The lease was satisfied by evidence the tool
manufactured a moment earlier.

The fix is to name the commit ourselves: ``--force-with-lease=<ref>:<expect>``,
where *expect* is the remote tip as it stood **before** the fetch. A colleague's
push then makes the remote disagree with what we named, and git refuses.

``--force-if-includes`` is the other candidate and is wrong here. It proves the
remote tip is reachable from the local branch's reflog, and a worktree this tool
materialised on demand has no reflog to speak of — only the zero-old
``branch: Created from refs/remotes/origin/<branch>`` entry, which does not
count. It rejects the routine case (a fresh worktree, a fresh clone, an expired
reflog) while the explicit lease accepts all three and still refuses the
clobber.

Two values of *expect* are legal and they are not interchangeable:

* a **full SHA** — the remote must still be at that commit;
* the **empty string** — the remote must not have the ref at all, which is the
  only form that can create a branch on its first push.

Passing a SHA for a ref the remote does not have fails with ``stale info``, and
passing the empty string for a ref it does have fails the same way. Neither is
recoverable mid-push, so ``resolve`` is the only place that chooses between
them, and it chooses by asking whether the ref is there.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass

from git import client as git_client

from . import inspect as rebase_inspect

# The empty expect: "the remote must not already have this ref". Spelled once
# because it is a real value in the lease grammar rather than a missing one,
# and a bare "" at a call site reads like an oversight.
CREATES_THE_REF = ""


@dataclass(frozen=True)
class PushLease:
    """What the remote must hold for this branch's force-push to be allowed."""

    branch: str
    expect: str

    @property
    def args(self) -> tuple[str, ...]:
        """The push flags carrying this lease.

        One token, fully spelled: ``refs/heads/`` rather than the short name so
        the ref cannot be resolved against something else, and the whole thing
        in one argument because that is the syntax git parses.
        """
        return (f"--force-with-lease=refs/heads/{self.branch}:{self.expect}",)

    @property
    def creates_the_ref(self) -> bool:
        """Whether this lease asserts the remote has no such branch yet."""
        return self.expect == CREATES_THE_REF


def remembered_tip(cwd: str, branch: str) -> str:
    """The remote tip as this checkout last saw it, read before any fetch.

    The remote-tracking ref, not the local branch: with unpushed commits the
    local tip is ahead of the remote, and naming it in a lease fails every
    legitimate push with ``stale info``. The local ref is the fallback only for
    a branch that has no tracking ref at all, where it is the best evidence
    there is.

    Empty when neither ref resolves — a branch that exists nowhere but here.
    Call this *before* ``git fetch``; afterwards the tracking ref holds whatever
    the remote has now, which is the value that cannot be trusted.
    """
    for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
        sha = git_client.out("rev-parse", "--verify", "--quiet", ref, cwd=cwd)
        if sha:
            return sha
    return ""


def resolve(cwd: str, branch: str, remembered: str) -> PushLease | None:
    """The lease to push *branch* under, or None when we cannot name one.

    Called after the fetch, because whether the remote still has the ref is
    exactly what the fetch (with ``--prune``) has just established. The two
    legal shapes are chosen here and nowhere else:

    * the ref is gone from the remote — the empty expect, which creates it;
    * the ref is there — the tip we remembered from before the fetch.

    None when the ref is there and we remembered nothing to name. Pushing then
    would mean falling back either to the empty expect, which fails against a
    ref that exists, or to a bare lease, which is the clobber this module was
    written to stop. Refusing is the only honest answer, and the caller reports
    it rather than guessing.
    """
    if not rebase_inspect.ref_exists(cwd, f"refs/remotes/origin/{branch}"):
        return PushLease(branch=branch, expect=CREATES_THE_REF)
    if not remembered:
        return None
    return PushLease(branch=branch, expect=remembered)
