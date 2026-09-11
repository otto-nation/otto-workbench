"""The rendered fix summary, recorded whole.

`_summary_row_key` derives a published row's identity from its rendered cell
text, and the next round re-parses the comment this renderer wrote to recover
what it said. A change to how any cell renders is therefore a change to row
identity, and duplicates every row in the table on the following round.

Nothing else in the suite compares a *whole* rendered body. Every other
assertion over `_build_summary_body` is a substring check on a cell somebody
thought to name, so a change to a part nobody named passes all of them. That is
measured rather than assumed — against `test_review_threads.py`'s 722 tests:

- moving the padding inside the row's outer pipes (`| a |` → `|a |`), which
  changes every row key: **722 passed**, golden failed
- rewording one `_table_note` ("carried over" → "carried forward"): **722
  passed**, golden failed
- dropping the `@` from the reviewer cell: caught by both, via
  `TestBuildSummaryBody::test_reviewer_column_rendered`

Two of those three reach the published comment and change what the next round
reads back out of it.

The renderer reaches git two ways, and every body here closes both, so no case
below runs a subprocess or needs a fixture repo:

- `AddressingHistory`, for the commit that satisfied a thread. Closed by
  `wt_path=None`, and by the `_FrozenHistory` stub below.
- `_anchored_line`, for whether a cited line still points at the same code
  (`git diff --quiet`). Closed twice over: it is only reached when
  `read_sha` is set and differs from the rendered sha, and the rows here set
  the two equal — so `wt_path=None` is the backstop rather than the guard
  doing the work.

Keep it that way: a module that pulls a clock or a worktree into this render is
one that cannot be recorded.

The fixtures are byte-exact renderer output and are written, never hand-edited.
`summary_body_minimal.md` ends in a run of blank lines — the empty counts line
and its trailing separator — which an editor set to trim trailing whitespace on
save will silently eat.

Regenerate the fixtures by calling `_write_goldens(rt)` from a throwaway test in
this directory — the `rt` fixture only exists under pytest, and `conftest.py` is
the one place allowed to load a script from a path. Prose rather than a flag
for the same reason `test_mcp_server.py` uses prose: one regeneration idiom in
the repo is better than two.

A diff in one of these files is a change to the published summary format and is
read as one — check `_summary_row_key`, `_carried_over_rows` and
`_hand_written_rows` before accepting it. During the decomposition this render
is being split for, the golden must be **re-run and re-asserted, never
regenerated**: regenerating it records whatever the split produced and asserts
nothing about it.
"""

import sys
from pathlib import Path

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git.land import CommitStatus  # noqa: E402
from pr import attribution  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402
from pr import summary_model
from pr import summary_row
from pr import summary_publish
from pr import summary_render
from pr import summary_rounds
from pr import summary_scope  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN_FULL = FIXTURES / "summary_body_full.md"
GOLDEN_MINIMAL = FIXTURES / "summary_body_minimal.md"
GOLDEN_RAW = FIXTURES / "summary_body_raw_sections.md"
GOLDEN_UNCOMMITTED = FIXTURES / "summary_body_uncommitted.md"

_REPO = "owner/repo"
_PR = 42
# The tree every link in the golden is pinned to. A row whose `read_sha` matches
# keeps its line anchor without a worktree to check it against; a row leaving
# `read_sha` empty renders the whole-file link instead. Both shapes are in the
# full body, because the anchoring decision is part of row identity.
_LINK_SHA = "abc1234"
# When the summary being continued was last written. The two scoped rows carry
# no activity the run can date, which `RoundScope.covers` reads as quiet — so
# they are left to the comment that published them and render as notes.
_SCOPE_SINCE = "2026-06-01T00:00:00Z"


def _round_content(rt, **buckets):
    """A `RoundContent` from the buckets named, and no others.

    The same shape `test_review_threads.py` builds, spelled again here because
    that fixture is local to that module and this golden outlives the file it
    currently records.
    """
    comments = {
        k: list(buckets.pop(k, ()))
        for k in ("issue_comments", "review_body_comments")
    }
    return summary_model.RoundContent(
        by_outcome={
            FixOutcome(name): list(entries) for name, entries in buckets.items()
        },
        **comments,
    )


class _FrozenHistory:
    """`AddressingHistory` with its two git reads answered from a table.

    The renderer's only route to a subprocess. A stub rather than a branch
    because what is recorded here is how a framing *renders*, not the archaeology
    that produces one — `TestRowsResolveTheirOwnCommitAcrossHandLandedWork`
    covers that against a real repository.
    """

    def __init__(self, by_id):
        self._by_id = by_id

    def framing(self, entry, thread, acted=False):
        """The recorded framing for this entry, or the pre-existing reading.

        `acted` mirrors `AddressingHistory.framing` and is never passed by the
        renderer; it is here so the stub cannot drift out of shape with the
        object it stands in for.
        """
        return self._by_id.get(entry.id, attribution.AddressedFraming(False))


def _threads():
    """A thread per row that has one, so the Thread cell renders its permalink.

    The comment-item rows (`ic-`/`rb-` ids) deliberately have no entry: those
    anchor to the top-level comment they were split out of, which is the other
    half of `_summary_row_key`'s identity tiers.
    """
    return {
        f"t{n}": ReportThread(id=f"t{n}", comments=[{"databaseId": 100 + n}])
        for n in range(1, 14)
    }


def _full_body(rt):
    """One body reaching every branch that composes with the others.

    Fourteen entries over every outcome bucket, rendering thirteen rows: three
    are left to an earlier summary and render as notes instead, one is held
    back by a hand edit and renders as the published text rather than this
    round's, and two more arrive already rendered as `carried_over`. Every
    count in the header line, all four notes, both singular and plural in each
    pair, and the round-chain footer. The raw comment sections are suppressed
    here (`has_comment_items=True` says triage already split those into rows)
    and are recorded by `_raw_sections_body` instead.

    What is *not* here is the Action-cell wording matrix. `_summary_row_key`
    excludes cell 3 from all three of its identity tiers, and
    `TestGeneratedActionCell` and `TestActionCellOutcome` sweep the two
    generated families — `_fixed_status_text` over `CommitStatus`, and
    `HumanReason` — against the builders rather than transcribing them. The one
    wording neither sweeps is the unlinked `Deferred → <id>`, which
    `_uncommitted_body` records.
    """
    content = _round_content(
        rt,
        fixed=[
            # Stamped with the pass's own commit, which is what lets the Action
            # cell cite a SHA — `_attribute_commit` refuses to name one for an
            # unstamped entry, and an uncited fixed row is the less interesting
            # of the two shapes to record.
            CommentItem(id="t1", summary="fix the regex", reviewer="kgn",
                        file="a.py", line=10, read_sha=_LINK_SHA,
                        commit_sha=_LINK_SHA),
            CommentItem(id="t2", summary="fix two", reviewer="amp",
                        file="b.py", line=2, read_sha=_LINK_SHA,
                        commit_sha=_LINK_SHA),
        ],
        already_addressed=[
            # Held back by a hand edit below, so it leaves the counts entirely.
            CommentItem(id="t3", summary="use the helper", reviewer="amp",
                        file="c.py", line=7),
            # Satisfied before the reviewer spoke, so it stays "already
            # addressed" rather than being counted as a fix.
            CommentItem(id="t11", summary="already true upstream", reviewer="kgn",
                        file="k.py", line=12),
            # Satisfied *in response*, which `_build_summary_body` counts as a
            # fix even though the bucket says addressed — the one place a row's
            # count and its bucket deliberately disagree.
            CommentItem(id="t12", summary="fixed after the review", reviewer="amp",
                        file="l.py", line=3),
        ],
        dismissed=[
            CommentItem(id="t4", summary="nope | really", reviewer="kgn",
                        file="d.py", line=3),
            # No thread, no reviewer, no file: the fallback identity tier, and
            # the two `—` cells that go with it.
            CommentItem(id="unthreaded", summary="use a || b, not a | b"),
        ],
        settled_elsewhere=[
            CommentItem(id="t5", summary="resolved on the forge", reviewer="amp",
                        file="e.py", line=1),
        ],
        needs_human=[
            CommentItem(id="t6", summary="which one?", reviewer="kgn",
                        file="f.py", line=5, reason="contested"),
        ],
        # Folded into the same "need discussion" count as NEEDS_HUMAN by
        # `_NEEDS_A_PERSON`, and rendered by the same `HumanReason` prose. Both
        # buckets are here because the fold is the thing worth recording.
        declined=[
            CommentItem(id="t7", summary="premise is wrong", reviewer="amp",
                        file="g.py", line=8),
        ],
        deferred=[
            CommentItem(id="ic-900-0", summary="too big", reviewer="kgn",
                        file="j.py", line=6, source_id="900",
                        source_type="issue_comment"),
        ],
    )

    # Two rows an earlier summary holds and nobody has spoken on since: one
    # settled, one still open. They render as the two scoping notes rather than
    # as rows, so their keys must be the ones the renderer will derive — a
    # thread row keys on its own `#discussion_r` anchor.
    content.by_outcome[FixOutcome.SETTLED_ELSEWHERE].append(
        CommentItem(id="t9", summary="quiet and settled", reviewer="kgn",
                    file="h.py", line=2))
    # Two open ones against one settled, so the two notes carry different
    # numbers. Equal counts would render the same body if the renderer put a
    # key in the wrong bucket, and would leave `text.plural` untested on both
    # sides.
    content.by_outcome[FixOutcome.NEEDS_HUMAN].extend([
        CommentItem(id="t10", summary="quiet and open", reviewer="amp",
                    file="i.py", line=4),
        CommentItem(id="t13", summary="quiet and also open", reviewer="kgn",
                    file="m.py", line=9),
    ])
    quiet = ["#discussion_r109", "#discussion_r110", "#discussion_r113"]
    scope = summary_rounds.RoundScope(
        since=_SCOPE_SINCE,
        published_keys=frozenset(quiet),
        published_outcomes={
            quiet[0]: FixOutcome.SETTLED_ELSEWHERE,
            quiet[1]: FixOutcome.NEEDS_HUMAN,
            quiet[2]: FixOutcome.NEEDS_HUMAN,
        },
    )

    return summary_render.build_summary_body(
        content,
        attribution.CommitPushResult(sha=_LINK_SHA, status=CommitStatus.PUSHED, error=""),
        _REPO,
        _PR,
        _threads(),
        deferred_issue_id="ENG-456",
        deferred_issue_url="https://linear.app/team/issue/ENG-456",
        has_comment_items=True,
        head_sha=_LINK_SHA,
        carried_over=[
            "| [old row](https://github.com/owner/repo/pull/42#discussion_r999) "
            "| @kgn | `old.go:4` | Fixed in `9f2e1a0` |",
            "| [older row](https://github.com/owner/repo/pull/42#discussion_r998) "
            "| @amp | `old.go:9` | Deferred |",
        ],
        # One row this render can account for and still must not write: a human
        # rewrote its Action cell, so the published text is re-emitted in place
        # and the entry behind it drops out of the counts.
        hand_held=[
            summary_model.HeldRow(
                key="#discussion_r103",
                published=(
                    "| [use the helper](https://github.com/owner/repo/pull/42"
                    "#discussion_r103) | @amp | [`c.py`](https://github.com/"
                    "owner/repo/blob/abc1234/c.py) | Superseded \u2014 @amp "
                    "reproduced it independently |"
                ),
                replaced_by="Already addressed",
            ),
        ],
        wt_path=None,
        history=_FrozenHistory({
            "t12": attribution.AddressedFraming(True, sha="def5678"),
        }),
        scope=scope,
        chain=[
            summary_rounds.SummaryRound(
                number=1,
                url="https://github.com/owner/repo/pull/42#issuecomment-11"),
            summary_rounds.SummaryRound(
                number=2,
                url="https://github.com/owner/repo/pull/42#issuecomment-12"),
        ],
    )


def _empty_body(rt):
    """A round with nothing to put in the table.

    Two different rounds reach this same body — one with no entries at all, and
    one whose only content is a comment triage has already split into rows — so
    it is the shape a reader sees when the pass has nothing to report.
    """
    return summary_render.build_summary_body(
        _round_content(rt),
        attribution.CommitPushResult(sha="", status=CommitStatus.NO_CHANGES, error=""),
        _REPO,
        _PR,
        {},
        wt_path=None,
        history=_FrozenHistory({}),
    )


def _suppressed_body(rt):
    """Unseen top-level comments, suppressed because triage already split them.

    `has_comment_items` is the caller saying the raw sections would restate what
    the table already carries as rows. Same inputs as `_raw_sections_body`
    apart from that flag, so the pair isolates what the flag does.
    """
    return _raw_sections_body(rt, has_comment_items=True)


def _raw_sections_body(rt, *, has_comment_items: bool = False):
    """The two raw comment sections, which the full body suppresses.

    Covers what `_render_raw_comment_sections` does with a review body carrying
    a state badge, an issue comment without one, and a body whose first usable
    line is behind a multi-line HTML comment, a blank line and a heading —
    `_summarize_comment_body`'s three skips in one value.
    """
    return summary_render.build_summary_body(
        _round_content(
            rt,
            issue_comments=[
                {"id": "901", "user": "kgn", "body": "Can we add tests?"},
                {"id": "902", "user": "amp",
                 "body": "<!-- generated\nstill inside the comment -->\n\n"
                         "# Heading\nbody text"},
                # Already reported by an earlier summary, so `_unseen` drops it.
                {"id": "904", "user": "kgn", "body": "seen already", "seen": True},
            ],
            review_body_comments=[
                {"id": "903", "user": "amp", "body": "Needs refactor",
                 "state": "CHANGES_REQUESTED"},
            ],
        ),
        attribution.CommitPushResult(sha="", status=CommitStatus.NO_CHANGES, error=""),
        _REPO,
        _PR,
        {},
        has_comment_items=has_comment_items,
        wt_path=None,
        history=_FrozenHistory({}),
    )


def _uncommitted_body(rt):
    """A round that committed nothing, and a deferral with no issue URL.

    Two branches the full body cannot reach, both of which render *cells* and
    so are part of row identity. With no sha to link against, the File cell
    falls back to plain backticks rather than a blob permalink — the only
    shape in which `_row_location_key` sees a bare ``file:line``. And a
    deferral whose tracker id is known but whose URL is not renders the id
    unlinked, which is the one Action-cell wording neither
    `TestGeneratedActionCell` nor `TestActionCellOutcome` sweeps.
    """
    return summary_render.build_summary_body(
        _round_content(
            rt,
            deferred=[
                CommentItem(id="t1", summary="needs a plan", reviewer="kgn",
                            file="a.py", line=10),
                CommentItem(id="t2", summary="no line to cite", reviewer="amp",
                            file="b.py"),
            ],
        ),
        attribution.CommitPushResult(sha="", status=CommitStatus.NO_CHANGES, error=""),
        _REPO,
        _PR,
        _threads(),
        deferred_issue_id="ENG-789",
        has_comment_items=True,
        wt_path=None,
        history=_FrozenHistory({}),
    )


class TestTheRenderedSummaryIsRecorded:
    """Whole-body comparisons, because row identity is derived from the body.

    Three fixtures, because these are the shapes that cannot be folded into one
    another: the full table with every note under it, the two raw comment
    sections the full table suppresses, and the empty body a round with nothing
    to report produces — which two different rounds reach, so it is asserted
    from both.
    """

    def test_the_full_body_matches_the_golden(self, rt):
        assert _full_body(rt) == GOLDEN_FULL.read_text(encoding="utf-8")

    def test_a_round_with_nothing_to_say_matches_the_golden(self, rt):
        assert _empty_body(rt) == GOLDEN_MINIMAL.read_text(encoding="utf-8")

    def test_unseen_top_level_comments_match_the_golden(self, rt):
        assert _raw_sections_body(rt) == GOLDEN_RAW.read_text(encoding="utf-8")

    def test_a_round_with_no_commit_matches_the_golden(self, rt):
        assert _uncommitted_body(rt) == GOLDEN_UNCOMMITTED.read_text(
            encoding="utf-8")

    def test_decomposed_items_suppress_the_raw_sections(self, rt):
        """An unseen comment already split into rows adds no section of its own."""
        assert _suppressed_body(rt) == GOLDEN_MINIMAL.read_text(encoding="utf-8")

    def test_the_render_carries_no_state_between_calls(self, rt):
        """Two renders of one input agree.

        Narrower than it looks: the golden above is what answers ordering, and
        it passes at every `PYTHONHASHSEED`. What this adds is that nothing
        accumulates across calls — a counter, a memo, a list built at module
        scope — which a single assertion against a fixture cannot see.
        """
        assert _full_body(rt) == _full_body(rt)


def _write_goldens(rt) -> None:
    """Rewrite the fixtures from the current renderer.

    Called by hand when a rendering change is intended. Never call it to make a
    failing assertion pass during the decomposition — the whole value of these
    files is that they predate it.
    """
    FIXTURES.mkdir(exist_ok=True)
    GOLDEN_FULL.write_text(_full_body(rt), encoding="utf-8")
    GOLDEN_MINIMAL.write_text(_empty_body(rt), encoding="utf-8")
    GOLDEN_RAW.write_text(_raw_sections_body(rt), encoding="utf-8")
    GOLDEN_UNCOMMITTED.write_text(_uncommitted_body(rt), encoding="utf-8")
