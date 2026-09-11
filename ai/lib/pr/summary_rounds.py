"""Which already-published rows a round's summary may leave to an earlier one.

A cycle used to keep one comment and restate every thread it had ever covered,
so the newest summary was always the complete one — and unreadable past a
handful of threads, re-notifying every reviewer with mostly stale rows. A round
now describes itself and links back through the footer chain.

Leaving a row out is only safe while the record still holds it somewhere, so
this module is the arithmetic of that safety: what every summary comment on the
PR holds, what the comment being edited alone holds, what each row last reported,
and when a reviewer last spoke on the surface behind it.

What is not here: reading rows out of a body (`pr.summary_scope`), and deciding
whether to edit or post fresh (`pr.summary_publish`) — this is handed that
decision because it changes what the round may omit.
"""

# doc-group: publishing

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass

from pr import comments as pc
from pr import permalinks
from pr import summary_model
from pr import summary_scope
from pr.fix import FixOutcome
from pr.thread_models import CommentItem, ReportThread

@dataclass(frozen=True)
class SummaryRound:
    """One summary comment already on the PR, as the footer links it.

    `number` is the comment's place in the chain, oldest first, so the reader
    walking back from the newest summary can tell which round they are opening.
    """

    number: int
    url: str


@dataclass(frozen=True)
class RoundScope:
    """Which already-published rows this round's summary may leave out.

    A cycle used to keep one comment and restate every thread it had ever
    covered, so the newest summary was always the complete one. Complete and
    unreadable: past a handful of threads nobody can tell this round's outcome
    from one settled three rounds ago, and each repost re-notifies the reviewers
    with mostly stale rows. So a round now describes itself, and the footer
    chain — see `SummaryRound` — is what carries the reader back through the
    rest.

    Leaving a row out is only safe while the record still holds it somewhere, so
    three questions have to come back "no" before one is dropped:

    ``published_keys`` is every row key on every summary comment the PR has. A
    key absent from it has never reached a reader, whatever local state believes,
    so it is always written. This is what keeps a row from being lost to a
    checkout whose state file cannot date it.

    ``target_keys`` is the rows only the comment this round is editing holds. An
    in-place edit rewrites that body wholesale, so dropping such a row deletes
    it from the record rather than deferring it to an earlier comment. A row an
    earlier summary also carries is not in that position and is not protected:
    dropping it loses nothing, because the reader finds it one link back.

    ``elsewhere_keys`` is the rows some comment other than the target holds,
    which is the same question read from the other side. `_carried_over_rows`
    needs it: scoping a row out of the body while the carry-forward step reads
    it as a round local state lost would put it straight back, verbatim.

    ``published_outcomes`` is the outcome each published row last reported, per
    `summary_model.action_outcome`. A round that changed a row's outcome writes it whoever
    else holds it — nobody has to speak for a deferred thread to become a fixed
    one, so the activity test alone would leave the new outcome on no summary at
    all and the record's newest word on the row the outcome it has replaced. A
    key with no entry is a row a person rewrote, and states nothing to differ
    from.

    ``since`` is when the newest summary's body was last written. A thread a
    reviewer has spoken on after that is this round's business again, however
    settled it looked. It dates the body rather than the comment because the
    keys it is read alongside come from the body: a summary edited in place over
    several rounds carries rows for threads opened long after it was posted, and
    dating it by `created_at` would call every one of them newer than the
    summary already holding it — restating the whole edited history each round,
    which is what this scoping exists to stop.

    A default-constructed scope covers everything: no keys are published, so no
    row can be left out. That is the reading a run with no summary comment to
    read wants, and the one `_build_summary_body` falls back to.
    """

    since: str = ""
    published_keys: frozenset[str] = frozenset()
    target_keys: frozenset[str] = frozenset()
    elsewhere_keys: frozenset[str] = frozenset()
    published_outcomes: Mapping[str, FixOutcome] = dataclasses.field(
        default_factory=dict)

    def covers(
        self, key: str, activity_at: str, outcome: FixOutcome | None = None,
    ) -> bool:
        """Whether this round's summary writes the row keyed `key`.

        `outcome` is what this round's render reports for the row. It is
        compared against the record's newest word rather than against the
        rendered cell, because one outcome has several wordings and a cell
        comparison would call every one of them a change.

        An entry the run cannot date — `activity_at` empty — reads as quiet
        rather than as new. A thread an earlier round settled stops being
        fetched, so undatable is the ordinary shape of exactly the row this
        scoping exists to stop restating, and treating it as new would leave the
        settled tail of a long PR restated every round. Nothing is lost to that
        reading: a key no summary comment holds has already returned True above,
        whatever the run can or cannot date about it.

        An undatable *summary* is the mirror case and goes the other way. With
        `since` empty every real timestamp sorts after it, so a round that
        cannot date the comment it is continuing writes every row it can date.
        """
        if key not in self.published_keys or key in self.target_keys:
            return True
        published = self.published_outcomes.get(key)
        if outcome is not None and published is not None and outcome != published:
            return True
        return activity_at > self.since


def comment_timestamps(
    issue_comments: list[dict], review_body_comments: list[dict],
) -> dict[str, str]:
    """When each top-level comment a summary row can be cut from was written.

    Keyed by the source id `permalinks.comment_item_source` reads off an entry, and
    stringified because that id is parsed out of a synthetic id while the
    listing carries it as a number.
    """
    stamps = {
        str(c.get("id", "")): str(c.get("created_at") or "")
        for c in issue_comments
    }
    stamps.update({
        str(c.get("id", "")): str(c.get("submitted_at") or "")
        for c in review_body_comments
    })
    return stamps


def entry_activity_at(
    entry: CommentItem,
    threads_by_id: dict[str, ReportThread],
    sources_at: dict[str, str],
) -> str:
    """When a reviewer last spoke on the surface this entry's row describes.

    Our own comments are left out for the same reason `_newest_reviewer_activity`
    leaves them out: the fix pass replies to a thread before it publishes, so a
    round counting its own replies would find every thread it touched newly
    active and restate the lot.

    "" means the run cannot date the entry — a thread this checkout did not
    fetch, an item whose source comment is not in the report. `RoundScope.covers`
    reads that as quiet, so such a row is left where it was published; a row no
    summary comment holds is written whatever this returns.
    """
    thread = threads_by_id.get(entry.id)
    if thread:
        mine = (thread.my_login or "").lower()
        return max(
            (
                str(c.get("createdAt") or "") for c in thread.comments
                if not mine or ((c.get("author") or {}).get("login") or "").lower() != mine
            ),
            default="",
        )
    return sources_at.get(permalinks.comment_item_source(entry).id, "")


def round_scope(marked: pc.MarkerHistory, answered: bool) -> RoundScope:
    """What this round's summary may leave to the comments already on the PR.

    ``answered`` is the publish decision, taken before the render because it
    changes what the render is allowed to omit: an edit rewrites its target, so
    the rows that target alone holds are in scope whatever they say, while a
    fresh comment replaces nothing and leaves every one of them where it is.

    Outcomes are read oldest comment first so the newest wins, the same order
    and for the same reason as `_hand_written_rows`: that is the one a reader
    arriving at the chain sees. A row whose newest cell is a person's states no
    outcome and gets no entry, rather than falling back to the generated cell
    an earlier round wrote under it.

    ``since`` dates the target's body, so it takes ``updated_at`` and falls back
    to ``created_at`` for a listing that carried no edit time. The keys beside it
    are read out of that body, and the two have to describe the same moment —
    see `RoundScope`.
    """
    target = marked.newest
    elsewhere = frozenset(
        summary_scope.row_key(row)
        for comment in marked.comments[:-1]
        for row in summary_scope.table_rows(comment.body)
    )
    target_own = frozenset(
        summary_scope.row_key(row) for row in summary_scope.table_rows(target.body))
    outcomes: dict[str, FixOutcome | None] = {}
    for body in marked.bodies:
        for row in summary_scope.table_rows(body):
            outcomes[summary_scope.row_key(row)] = summary_model.action_outcome(summary_scope.row_action_cell(row))
    return RoundScope(
        since=target.updated_at or target.created_at,
        published_keys=target_own | elsewhere,
        target_keys=frozenset() if answered else target_own - elsewhere,
        elsewhere_keys=elsewhere,
        published_outcomes={
            key: outcome for key, outcome in outcomes.items() if outcome is not None
        },
    )


