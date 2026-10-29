"""An incremental round reports no new nit on a file it did not change, and fixes none."""

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review.outcome  # noqa: E402
import review.phases  # noqa: E402
import review.pipeline  # noqa: E402
import review.steps  # noqa: E402
from review.convergence import (  # noqa: E402
    drop_out_of_delta_nits, fix_candidates, fix_work_set, held_nits, new_nit_ids,
    prior_ids,
)
from core.phases import Phase  # noqa: E402
from review.ingest import ArtifactOutcome  # noqa: E402
from review.paths import phase_output_path, read_review_meta  # noqa: E402
from review.record import Disposition, ReviewRecord  # noqa: E402
from review.store import StoredReview, read_review, write_review  # noqa: E402
from review.types import PreflightData, ReviewMeta  # noqa: E402
from review.verify import PostProcessed  # noqa: E402
from review_convergence_support import (  # noqa: E402
    committed_repo, finding, head, merged, review_job, single_agent_record,
)
from review_fix_pass_support import _run, _tracking  # noqa: E402

NIT_OUT = finding("N1", "rename", path="old.py", line=2)
NIT_IN = finding("N2", "rename", path="new.py", line=2)
MUST_OUT = finding("M1", "bug", path="old.py", line=4)
CARRIED_NIT = finding("N3", "carried", path="old.py", line=6, sid="abc")
RESTATED_NIT = finding("N4", "restated", path="old.py", line=8, stable_id="sidprior")
WHOLE_PR = finding("I1", "naming across the PR")
PRIOR = StoredReview(
    ReviewRecord(findings=(finding("N9", "restated", path="old.py", line=8,
                                   stable_id="sidprior"),)),
    ReviewMeta(head_sha="p" * 40),
)


def _preflight(delta=("new.py",)):
    return PreflightData(diff="", commit_log="", file_contents={}, file_permissions={},
                         instructions_md="", architecture_md="",
                         delta_files=list(delta), prior_head_sha="p" * 40)


def _incremental(tmp_path, prior=None):
    return review_job(tmp_path, tmp_path / "r", head_sha="h" * 40, prior=prior,
                      preflight=_preflight())


def test_a_new_nit_outside_the_delta_is_dropped():
    record = ReviewRecord(findings=(NIT_OUT, NIT_IN, MUST_OUT, CARRIED_NIT, WHOLE_PR))
    out = drop_out_of_delta_nits(record, ["new.py"], frozenset({"abc"}))
    assert [f.id for f in out.record.findings] == ["N2", "M1", "N3", "I1"]
    assert [f.id for f in out.dropped] == ["N1"]


def test_a_sid_no_prior_round_held_does_not_make_a_nit_carried():
    """Synthesis sees every merged finding with a sid — its own stable id where it had none."""
    stamped = replace(NIT_OUT, sid=NIT_OUT.stable_id)
    out = drop_out_of_delta_nits(ReviewRecord(findings=(stamped,)), ["new.py"], frozenset({"abc"}))
    assert [f.id for f in out.dropped] == ["N1"]
    assert new_nit_ids(ReviewRecord(findings=(stamped,)), frozenset()) == frozenset({"sidn1"})


def test_a_restated_prior_nit_on_an_unchanged_file_is_kept(tmp_path, monkeypatch):
    """A re-run group restates a prior nit with a fresh ID and no sid, but its stable id."""
    monkeypatch.setattr(review.steps, "_phase_merge", lambda *a, **k: merged(
        ReviewRecord(findings=(RESTATED_NIT, NIT_OUT)), fresh=2))
    out = review.steps._merge_round(_incremental(tmp_path, PRIOR), [], [], ())
    assert [f.id for f in out.record.findings] == ["N4"]


def test_prior_ids_holds_stable_ids_and_sids(tmp_path):
    prior = StoredReview(ReviewRecord(findings=(
        finding("S1", "x", path="a.py", line=1, stable_id="s1", sid="t1"),
        finding("S2", "y", path="a.py", line=2, stable_id=""),
    )), ReviewMeta())
    assert prior_ids(_incremental(tmp_path, prior)) == frozenset({"s1", "t1"})
    assert prior_ids(_incremental(tmp_path)) == frozenset()


def test_nothing_to_drop_returns_the_record_untouched():
    record = ReviewRecord(findings=(NIT_IN,))
    assert drop_out_of_delta_nits(record, ["new.py"], frozenset()).record is record


def test_the_merge_round_drops_them_and_counts_what_is_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(review.steps, "_phase_merge", lambda *a, **k: merged(
        ReviewRecord(findings=(NIT_OUT, NIT_IN)), fresh=2))
    out = review.steps._merge_round(_incremental(tmp_path), [], [], ())
    assert [f.id for f in out.record.findings] == ["N2"] and out.fresh == 1
    assert "old.py" not in out.text


def test_the_merge_round_forwards_merge_keywords(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(review.steps, "_phase_merge",
                        lambda *a, **k: seen.update(k) or merged(fresh=0))
    review.steps._merge_round(_incremental(tmp_path), [], [], (), declinable=frozenset({"x"}))
    assert seen == {"declinable": frozenset({"x"})}


def test_a_full_round_keeps_every_nit(tmp_path, monkeypatch):
    monkeypatch.setattr(review.steps, "_phase_merge", lambda *a, **k: merged(
        ReviewRecord(findings=(NIT_OUT,)), fresh=1))
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40)
    assert [f.id for f in review.steps._merge_round(job, [], [], ()).record.findings] == ["N1"]


def test_carry_keeps_the_fresh_count():
    out = merged(fresh=0).carry(ReviewRecord(findings=(CARRIED_NIT,)))
    assert out.fresh == 0


def test_the_single_agent_path_drops_them_too(tmp_path, monkeypatch):
    job = _incremental(tmp_path)
    seen = {}
    monkeypatch.setattr(review.pipeline, "build_prompt", lambda *a, **k: "PROMPT")

    def agent(inv, throttle=None):
        Path(phase_output_path(job.review_file, Phase.SINGLE)).write_text("{}")
        Path(inv.session_log).write_text("")
        return 0
    monkeypatch.setattr(review.phases, "run_agent", agent)
    single_agent_record(monkeypatch, ReviewRecord(findings=(NIT_OUT, NIT_IN)))
    monkeypatch.setattr(review.pipeline, "_should_disprove", lambda *a: False)
    monkeypatch.setattr(review.pipeline, "_post_process_review",
                        lambda j, record, **k: seen.update(record=record))
    review.pipeline.run_single_agent(job)
    assert [f.id for f in seen["record"].findings] == ["N2"]


def test_new_nit_ids_reads_the_sid_and_never_counts_a_finding_with_no_identity():
    record = ReviewRecord(findings=(
        finding("N1", "new nit", path="a.py", line=1),
        finding("N2", "carried, reworded", path="a.py", line=2, sid="t2"),
        finding("N3", "restated", path="b.py", line=1, stable_id="sidkept"),
        finding("N4", "carried, no path, no identity", stable_id=""),
        finding("M1", "bug", path="a.py", line=3),
    ))
    assert new_nit_ids(record, frozenset({"sidkept", "t2"})) == frozenset({"sidn1"})


def test_an_incremental_round_adds_to_the_held_set_and_a_full_one_clears_it(tmp_path):
    job = _incremental(tmp_path, StoredReview(ReviewRecord(), ReviewMeta(held_nits=("sidold",))))
    job.new_nits = frozenset({"sidnew"})
    assert held_nits(job, incremental=True) == ("sidnew", "sidold")
    assert held_nits(job, incremental=False) == ()


def test_new_nits_stay_open_but_are_not_handed_to_the_fix_pass(tmp_path):
    """D13: a held nit is reported, not fixed; an unheld one of the same severity is fixed."""
    wt = committed_repo(tmp_path / "wt", {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    job = review_job(wt, tmp_path / "reviews", head_sha="abc1234", files=["a.py", "b.py"])
    write_review(Path(job.review_file), ReviewRecord(findings=(
        finding("N1", "new nit", path="a.py", line=1, checkbox=True),
        finding("M1", "bug", path="a.py", line=1, checkbox=True),
        finding("N2", "carried nit", path="b.py", line=1, checkbox=True, stable_id="sidkept"),
    )), ReviewMeta(held_nits=("sidn1",)))
    assert _work_set(job) == {"M1", "N2"}
    left = {f.id: f for f in read_review(Path(job.review_file)).record.findings}
    assert left["N1"].disposition is Disposition.OPEN


def _work_set(job) -> set[str]:
    """The finding ids `run_fix_pass` put on the agent's checklist."""
    seen = {}
    _run(job, {"M1": "fixed", "N1": "fixed", "N2": "fixed"},
         work=lambda: seen.update(text=_tracking(job).read_text()))
    return {fid for fid in ("M1", "N1", "N2") if f"fix:{fid}" in seen["text"]}


def _published(tmp_path, wt, record, *, prior, incremental=True):
    """One round's publish, as `_post_process_review` makes it."""
    job = review_job(wt, tmp_path / "reviews", head_sha=head(wt), files=["a.py", "b.py"],
                     prior=prior, preflight=_preflight(("a.py",)) if incremental else None)
    review.outcome._publish(job, review.outcome._reconcile_and_verify(job, record))
    return job


def test_a_held_nit_stays_out_of_fix_work_until_a_full_round(tmp_path, monkeypatch):
    """Round N holds its new nit; round N+1 carries it and still holds it; a full round hands it over.

    Post-processing is stubbed to do the one thing that matters here — clear
    every sid — so the case also pins that newness is read before it does.
    """
    monkeypatch.setattr(review.outcome, "record_prior_findings", lambda *a, **k: None)
    monkeypatch.setattr(review.outcome, "post_process_record", lambda record, *a, **k: PostProcessed(
        replace(record, findings=tuple(replace(f, sid="") for f in record.findings)), None))
    wt = committed_repo(tmp_path / "wt", {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    nit = finding("N1", "new nit", path="a.py", line=1, checkbox=True)
    must = finding("M1", "bug", path="a.py", line=1, checkbox=True)
    # Carried, and reworded into a stable id the prior does not hold: its sid names a prior finding.
    carried = finding("N2", "carried nit", path="b.py", line=1, checkbox=True, sid="t9")
    base = StoredReview(ReviewRecord(findings=(
        finding("N7", "earlier wording", path="b.py", line=1, stable_id="t9"),
    )), ReviewMeta(head_sha="p" * 40))

    round_n = _published(tmp_path, wt, ReviewRecord(findings=(nit, must, carried)), prior=base)
    assert read_review_meta(Path(round_n.artifact_dir)).held_nits == ("sidn1",)

    round_next = _published(tmp_path, wt, ReviewRecord(findings=(replace(nit, sid="t1"), must)),
                            prior=read_review(Path(round_n.review_file)))
    assert read_review_meta(Path(round_next.artifact_dir)).held_nits == ("sidn1",)
    assert _work_set(round_next) == {"M1"}

    full = _published(tmp_path, wt, ReviewRecord(findings=(nit, must)),
                      prior=read_review(Path(round_next.review_file)), incremental=False)
    assert read_review_meta(Path(full.artifact_dir)).held_nits == ()
    assert _work_set(full) == {"M1", "N1"}


def test_a_must_or_should_finding_in_the_held_set_stays_in_fix_work():
    """A colliding stable id must not withhold Must/Should — only Nit/Idiom."""
    must = finding("M1", "bug", path="a.py", line=1, stable_id="held").view()
    should = finding("S1", "should", path="a.py", line=2, stable_id="held").view()
    nit = finding("N1", "nit", path="a.py", line=3, stable_id="held").view()
    assert [f.id for f in fix_work_set([must, should, nit], ("held",))] == ["M1", "S1"]


def test_fix_candidates_drops_checked_declined_skipped_and_held():
    """One owner: open + not declined + not skipped + not held."""
    open_must = finding("M1", "bug", path="a.py", line=1).view()
    declined = finding("M2", "tradeoff", path="a.py", line=2).view()
    declined.declined = True
    skipped = finding("S1", "person", path="a.py", line=3).view()
    skipped.skipped = True
    held = finding("N1", "nit", path="a.py", line=4, stable_id="held").view()
    fixed = finding("M3", "done", path="a.py", line=5).view()
    fixed.checked = True
    got = fix_candidates(
        [open_must, declined, skipped, held, fixed], ("held",), quiet=True,
    )
    assert [f.id for f in got] == ["M1"]


def test_synthesis_drops_out_of_delta_nits_on_an_incremental_round(tmp_path, monkeypatch):
    """Synthesis may add cross-cutting nits; on an incremental round they are dropped too."""
    job = _incremental(tmp_path)
    seen = {}
    monkeypatch.setattr(review.steps, "build_prompt", lambda *a, **k: "prompt")

    def agent(inv, **kwargs):
        Path(phase_output_path(job.review_file, Phase.SYNTHESIS)).write_text("{}")
        Path(inv.session_log).write_text("")
        return 0
    monkeypatch.setattr(review.phases, "run_agent", agent)
    artifact = SimpleNamespace(to_record=lambda **_: ReviewRecord(findings=(NIT_OUT, NIT_IN)),
                               focus=())
    monkeypatch.setattr(review.steps, "ingest_artifact",
                        lambda *a, **k: ArtifactOutcome(artifact))
    monkeypatch.setattr(review.steps, "_post_process_review",
                        lambda j, record, **k: seen.update(record=record))
    review.steps._phase_synthesis(job, "", 2, merged(ReviewRecord(findings=(NIT_IN,)), fresh=1))
    assert [f.id for f in seen["record"].findings] == ["N2"]


def test_single_agent_disprove_sees_the_filtered_record(tmp_path, monkeypatch):
    job = _incremental(tmp_path)
    seen = {}
    monkeypatch.setattr(review.pipeline, "build_prompt", lambda *a, **k: "PROMPT")

    def agent(inv, throttle=None):
        Path(phase_output_path(job.review_file, Phase.SINGLE)).write_text("{}")
        Path(inv.session_log).write_text("")
        return 0
    monkeypatch.setattr(review.phases, "run_agent", agent)
    single_agent_record(monkeypatch, ReviewRecord(findings=(NIT_OUT, NIT_IN)))
    monkeypatch.setattr(review.pipeline, "_should_disprove", lambda *a: True)
    monkeypatch.setattr(
        review.pipeline, "_phase_disprove",
        lambda j, record: seen.update(record=record) or SimpleNamespace(record=record),
    )
    monkeypatch.setattr(review.pipeline, "_post_process_review", lambda *a, **k: None)
    review.pipeline.run_single_agent(job)
    assert [f.id for f in seen["record"].findings] == ["N2"]
