"""Synthesis in `review.steps`: the phase itself and the fallback around it."""

import json
import sys
from pathlib import Path

import pytest

from conftest import git_out

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

from core.phases import Phase
from review.record import Disposition, LedgerLine, ProseSection, ReviewRecord, load_record
from review.steps import Merged
from review.store import StoredReview
from review.types import PriorDisposition, ReviewMeta
from review.verdict import FALLBACK_SUMMARY, MECHANICAL_NOTE
from review.verify import PostProcessed
from review_record_support import finding, record


class TestPhaseSynthesis:
    def _make_job(self, ro, tmp_path, mode="pr"):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode),
        )

    @staticmethod
    def _patch_pipeline(monkeypatch, **overrides):
        """Patch the module-level imports `_phase_synthesis` reaches through.

        The synthesis phase spans three modules: it builds its own prompt and
        post-processes its own findings in `review.steps`, writes the result
        through `review.outcome`, but invokes the agent through `PhaseRunner`,
        whose bindings live in `review.phases`. Each name is patched on the
        module that owns it — an explicit map, not a guess, because a name
        this maps wrong lands the patch on a module the code under test never
        reads and the test passes having mocked nothing.
        """
        import agent.retry
        import review.outcome
        import review.phases
        import review.steps
        owners = {
            "build_prompt": review.steps,
            "post_process_record": review.outcome,
            "run_agent": review.phases,
            # `_retry_missing_output` is `agent.retry.retry_missing_output`
            # (aliased in `review.retry`), which recovers through its own
            # `try_recover_output` binding, not `review.phases`' — that one is
            # read only by `_review_group`, which this phase never calls.
            "try_recover_output": agent.retry,
        }
        defaults = {
            "build_prompt": lambda *a, **kw: "mock prompt",
            "post_process_record": lambda record, *a, **kw: PostProcessed(record, None),
        }
        defaults.update(overrides)
        for name, func in defaults.items():
            if name not in owners:
                raise ValueError(f"_patch_pipeline has no owner mapped for {name!r}")
            monkeypatch.setattr(owners[name], name, func)

    def test_successful_synthesis(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(
                json.dumps({"summary": "Looks good.", "verdict": "Approve."}))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, Merged(record()))

        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert FALLBACK_SUMMARY not in result

    def test_a_successful_synthesis_quoting_both_literals_has_no_diagnosis(
        self, ro, tmp_path, monkeypatch,
    ):
        """A genuine agent review that discusses the fallback machinery in prose
        is not misread as one — the decision is made where the write happened,
        not by scanning the document for these literals."""
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": (
                    f"Looks good. Quotes {MECHANICAL_NOTE} and "
                    f"{FALLBACK_SUMMARY} in prose."
                ),
                "verdict": "Approve.",
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        result = ro._phase_synthesis(job, "", 3, Merged(record()))

        assert result.diagnosis is None

    def test_cost_comes_from_the_session_log(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(
                json.dumps({"summary": "Looks good.", "verdict": "Approve."}))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        assert ro._phase_synthesis(job, "", 3, Merged(record())).cost == 2.5

    def test_a_fallen_back_synthesis_is_still_charged(self, ro, tmp_path, monkeypatch):
        """The agent ran and spent; the review file just came from the merge."""
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = record(finding("M1", "issue", path="file.go"))
        result = ro._phase_synthesis(job, "", 3, Merged(merged))

        assert FALLBACK_SUMMARY in Path(job.review_file).read_text()
        assert result.cost == 2.5
        assert result.diagnosis == ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)

    def test_rejected_synthesis_output_falls_back_to_the_merge(
        self, ro, tmp_path, monkeypatch,
    ):
        """A synthesis that wrote the render banner is refused and merged."""
        from review.render import BANNER

        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(BANNER)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        merged = record(finding("M1", "issue", path="file.go"))
        result = ro._phase_synthesis(job, "", 3, Merged(merged))

        assert FALLBACK_SUMMARY in Path(job.review_file).read_text()
        assert result.diagnosis.kind == ro.DiagnosisKind.MECHANICAL_FALLBACK
        assert "invalid_json" in (result.diagnosis.detail or "")

    def test_an_unpromptable_synthesis_falls_back_to_the_merge(
        self, ro, tmp_path, monkeypatch,
    ):
        """The findings are already on disk; only the write-up would not fit.

        Merging them mechanically keeps every finding and loses the prose,
        which is the same trade a synthesis agent that failed already makes —
        and strictly better than discarding a phase's worth of group reviews
        because their cover letter is over the budget.
        """
        from review.prompt import PromptTooLarge

        job = self._make_job(ro, tmp_path)
        invoked = []

        def boom(*_a, **_kw):
            raise PromptTooLarge("synthesis.md", 600_000)

        self._patch_pipeline(
            monkeypatch,
            build_prompt=boom,
            run_agent=lambda inv, **kw: invoked.append(inv),
        )

        merged = record(finding("M1", "issue", path="file.go"))
        result = ro._phase_synthesis(job, "", 3, Merged(merged))

        assert invoked == []
        written = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in written
        assert "file.go:1" in written
        assert result.diagnosis == ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)

    def test_agent_fails_no_output(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.session_log).write_text("")
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = record(finding("M1", "issue", path="file.go"))
        ro._phase_synthesis(job, "", 3, Merged(merged))

        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result

    def test_incomplete_output_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({"summary": ""}))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        merged = record(finding("S1", "cleanup", path="api.go", line=10))
        ro._phase_synthesis(job, "", 3, Merged(merged))

        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result

    def test_transient_error_retries_then_succeeds(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                Path(inv.session_log).write_text(
                    '{"type":"result","subtype":"success","is_error":true,'
                    '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
                )
                return 1
            Path(inv.output_path).write_text(
                json.dumps({"summary": "Looks good.", "verdict": "Approve."}))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, Merged(record()))

        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert FALLBACK_SUMMARY not in result
        assert len(calls) == 2

    def test_transient_error_retries_then_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = record(finding("M1", "issue", path="file.go"))
        ro._phase_synthesis(job, "", 3, Merged(merged))

        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result
        assert len(calls) == 2

    def test_non_transient_error_does_not_retry(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"agent error: something broke"}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = record(finding("M1", "issue", path="file.go"))
        ro._phase_synthesis(job, "", 3, Merged(merged))

        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result
        assert len(calls) == 1

    def test_a_synthesis_without_a_summary_gets_a_repair_turn(self, ro, tmp_path, monkeypatch):
        """X4: an incomplete synthesis is repaired once rather than merged mechanically."""
        job = self._make_job(ro, tmp_path)
        docs = iter([json.dumps({"summary": ""}),
                     json.dumps({"summary": "Looks good.", "verdict": "Approve — clean."})])
        prompts = []

        def mock_invoke(inv, **kwargs):
            prompts.append(inv.prompt)
            Path(inv.output_path).write_text(next(docs))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        result = ro._phase_synthesis(job, "", 3, Merged(ReviewRecord()))
        assert len(prompts) == 2 and "$.summary" in prompts[1]
        assert result.diagnosis is None
        assert "Looks good." in Path(job.review_file).read_text()

    def test_synthesis_keeps_the_review_gaps_section(self, ro, tmp_path, monkeypatch):
        """X3: a section the harness wrote survives an agent that could not copy it."""
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({"summary": "s", "verdict": "Approve"}))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        gaps = ReviewRecord(extra=(ProseSection("Review gaps", "- g2: agent failed"),))
        ro._phase_synthesis(job, "", 3, Merged(gaps))
        assert "## Review gaps" in Path(job.review_file).read_text()

    def test_synthesis_is_shown_the_merged_groups_as_json(self, ro, tmp_path, monkeypatch):
        seen = {}
        job = self._make_job(ro, tmp_path)
        self._patch_pipeline(
            monkeypatch,
            build_prompt=lambda *a, **kw: seen.update(kw) or "mock prompt",
            run_agent=lambda inv, **kw: 1,
        )
        merged = Merged(record(finding("N1", "x")))
        ro._phase_synthesis(job, "", 3, merged)
        body = seen["merged_content"].removeprefix("```json\n").removesuffix("\n```")
        assert json.loads(body)["findings"][0]["path"] == "a.py"

    def test_synthesis_is_told_which_groups_failed(self, ro, tmp_path, monkeypatch):
        """D9: `GroupArtifact` has no field for the gaps, so they follow the JSON as markdown."""
        seen = {}
        job = self._make_job(ro, tmp_path)
        self._patch_pipeline(
            monkeypatch,
            build_prompt=lambda *a, **kw: seen.update(kw) or "mock prompt",
            run_agent=lambda inv, **kw: 1,
        )
        gaps = ReviewRecord(extra=(ProseSection("Review gaps", "- svc-card: agent hit max turns (10)"),))
        ro._phase_synthesis(job, "", 3, Merged(gaps))
        shown = seen["merged_content"]
        assert shown.startswith("```json\n")
        assert "## Review gaps" in shown and "- svc-card: agent hit max turns (10)" in shown

    def test_synthesis_keeps_a_decline_only_for_a_prior_finding(self, ro, tmp_path, monkeypatch):
        """D19: synthesis copies declines forward, and the runner checks each one.

        Nits, so the evidence gate (must-fix and should-fix only) leaves both
        alone: `a.py` is not on disk under `wt_path`, and an S finding would be
        dropped as "file not found" before the decline guard mattered.
        """
        job = self._make_job(ro, tmp_path)
        job.wt_path = str(tmp_path)
        job.prior = StoredReview(record(finding("N1", "kept", stable_id="3f9a2c1d")), ReviewMeta())
        carried = {"id": "N1", "path": "a.py", "line": 1, "body": "kept",
                   "sid": "3f9a2c1d", "declined": "documented"}
        invented = {**carried, "id": "N2", "body": "invented", "sid": "00000000"}

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({"summary": "s", "findings": [carried, invented]}))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        ro._phase_synthesis(job, "", 3, Merged(ReviewRecord()))
        by_body = {f.body: f.disposition for f in load_record(tmp_path).findings}
        assert by_body == {"kept": Disposition.DECLINED, "invented": Disposition.OPEN}

    def test_a_carried_fixed_finding_stays_fixed_through_synthesis(self, ro, tmp_path, monkeypatch):
        """A FIXED finding the merge carried is not shown to synthesis and stays FIXED.

        `as_group_artifact` would re-open it, so the synthesis view drops it and
        the runner merges it back by stable_id after the agent writes.
        """
        seen = {}
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({"summary": "s", "verdict": "Approve"}))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(
            monkeypatch,
            build_prompt=lambda *a, **kw: seen.update(kw) or "mock prompt",
            run_agent=mock_invoke,
        )
        fixed = finding("N1", "already fixed", stable_id="3f9a2c1d",
                        disposition=Disposition.FIXED)
        ro._phase_synthesis(job, "", 3, Merged(record(fixed)))
        loaded = load_record(tmp_path)
        assert [f.disposition for f in loaded.findings] == [Disposition.FIXED]
        assert "already fixed" not in seen.get("merged_content", "")

    def test_restored_findings_never_share_an_id(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "s", "verdict": "Approve",
                "findings": [
                    {"id": "N1", "path": "a.py", "line": 1, "body": "synth one"},
                    {"id": "N2", "path": "a.py", "line": 2, "body": "synth two"},
                ],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        held = record(
            finding("N1", "skipped thing", path="b.py",
                    disposition=Disposition.SKIPPED),
            finding("N2", "already fixed", path="c.py",
                    disposition=Disposition.FIXED),
        )
        ro._phase_synthesis(job, "", 3, Merged(held))
        ids = [f.id for f in load_record(tmp_path).findings]
        assert len(ids) == 4
        assert len(set(ids)) == 4

    @pytest.mark.parametrize(
        "emitted",
        [
            {"id": "N1", "path": "a.py", "line": 1, "body": "already fixed"},
            {"id": "N1", "path": "a.py", "line": 1, "body": "reworded",
             "sid": "3f9a2c1d"},
        ],
        ids=["same-anchor", "same-sid"],
    )
    def test_a_re_emitted_held_finding_keeps_its_disposition(
        self, ro, tmp_path, monkeypatch, emitted,
    ):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "s", "verdict": "Approve", "findings": [emitted],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        held = finding(
            "N1", "already fixed", sid="3f9a2c1d",
            disposition=Disposition.FIXED,
        )
        ro._phase_synthesis(job, "", 3, Merged(record(held)))
        loaded = load_record(tmp_path).findings
        assert len(loaded) == 1
        assert loaded[0].disposition is Disposition.FIXED
        assert loaded[0].body == "already fixed"

    def test_re_emitted_held_findings_take_the_copy_ids(
        self, ro, tmp_path, monkeypatch,
    ):
        """Held N1 SKIPPED (sid) and N2 FIXED (c.py:7) vs synthesis N1–N4.

        Synthesis emits its own N1/N2, N3 as the same-anchor copy of held N2,
        and N4 as a reworded copy with the SKIPPED finding's sid. Each held
        finding takes the copy's id so the slot stays unique and summary prose
        that names the copy still names it.
        """
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "see [N3]", "verdict": "Approve",
                "findings": [
                    {"id": "N1", "path": "a.py", "line": 1, "body": "synth one"},
                    {"id": "N2", "path": "a.py", "line": 2, "body": "synth two"},
                    {"id": "N3", "path": "c.py", "line": 7, "body": "already fixed"},
                    {"id": "N4", "path": "d.py", "line": 1, "body": "reworded",
                     "sid": "aaaa1111"},
                ],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        held = record(
            finding("N1", "skipped thing", path="b.py", sid="aaaa1111",
                    disposition=Disposition.SKIPPED),
            finding("N2", "already fixed", path="c.py", line=7,
                    disposition=Disposition.FIXED),
        )
        ro._phase_synthesis(job, "", 3, Merged(held))
        loaded = load_record(tmp_path)
        ids = [f.id for f in loaded.findings]
        assert ids == ["N1", "N2", "N3", "N4"]
        assert len(set(ids)) == 4
        by_id = {f.id: f for f in loaded.findings}
        assert by_id["N3"].disposition is Disposition.FIXED
        assert by_id["N3"].body == "already fixed"
        assert by_id["N4"].disposition is Disposition.SKIPPED
        assert by_id["N4"].sid == "aaaa1111"
        assert loaded.summary == "see [N3]"

    def test_a_carried_held_finding_takes_the_copy_slot(
        self, ro, tmp_path, monkeypatch,
    ):
        """Groups N1/N2 plus carried N3 SKIPPED/N4 FIXED vs synthesis N1–N4.

        Synthesis copies held N4 into N3 and emits a new N4. The held FIXED
        finding takes the copy's N3 slot; the unmatched SKIPPED is appended.
        """
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "see [N3]", "verdict": "Approve",
                "findings": [
                    {"id": "N1", "path": "a.py", "line": 1, "body": "from groups one"},
                    {"id": "N2", "path": "a.py", "line": 2, "body": "from groups two"},
                    {"id": "N3", "path": "c.py", "line": 7, "body": "already fixed"},
                    {"id": "N4", "path": "a.py", "line": 4, "body": "new from synth"},
                ],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        merged = record(
            finding("N1", "from groups one"),
            finding("N2", "from groups two", line=2),
            finding("N3", "skipped thing", path="b.py",
                    disposition=Disposition.SKIPPED),
            finding("N4", "already fixed", path="c.py", line=7,
                    disposition=Disposition.FIXED),
        )
        ro._phase_synthesis(job, "", 3, Merged(merged))
        loaded = load_record(tmp_path)
        ids = [f.id for f in loaded.findings]
        assert ids == ["N1", "N2", "N3", "N4", "N5"]
        assert len(set(ids)) == 5
        by_id = {f.id: f for f in loaded.findings}
        assert by_id["N3"].disposition is Disposition.FIXED
        assert by_id["N3"].body == "already fixed"
        assert by_id["N4"].body == "new from synth"
        assert by_id["N5"].disposition is Disposition.SKIPPED
        assert loaded.summary == "see [N3]"

    def test_a_held_finding_re_emitted_at_another_severity_keeps_its_severity(
        self, ro, tmp_path, monkeypatch,
    ):
        """Held S1 re-emitted as N-something via sid is not put in that slot.

        Prose referring to that copy's id may then dangle (accepted, rare):
        the copy's id becomes a gap that renumbering closes.
        """
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "see [N1]", "verdict": "Approve",
                "findings": [
                    {"id": "N1", "path": "a.py", "line": 1, "body": "reworded",
                     "sid": "aaaa1111"},
                    {"id": "N2", "path": "a.py", "line": 2, "body": "other"},
                ],
            }))
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)
        held = record(finding(
            "S1", "skipped should", sid="aaaa1111",
            disposition=Disposition.SKIPPED,
        ))
        ro._phase_synthesis(job, "", 3, Merged(held))
        loaded = load_record(tmp_path)
        ids = [f.id for f in loaded.findings]
        assert len(ids) == len(set(ids))
        skipped = [f for f in loaded.findings if f.disposition is Disposition.SKIPPED]
        assert len(skipped) == 1
        assert skipped[0].severity == "S"
        assert skipped[0].id.startswith("S")


def test_carried_findings_never_share_an_id():
    base = Merged(record(finding("N1", "from groups")))
    extra = record(finding(
        "N1", "from prior", path="b.py", disposition=Disposition.FIXED,
    ))
    ids = [f.id for f in base.carry(extra).record.findings]
    assert len(ids) == 2
    assert len(set(ids)) == 2


class TestSynthesisIsAskedAboutWhatTheGroupsPassedOver:
    """A prior finding the groups never mentioned reaches the last agent that can act.

    Before this, reconciliation ran only after synthesis had written the
    document, so a finding whose subject the tree still held left the run as a
    warning and left the review saying nothing about it — which the next round
    reads as the finding no longer existing.
    """

    BEFORE = "func handle() {\n    rows, _ := db.Query(sql)\n}\n"
    PRIOR = record(finding(
        "M1", "`rows, _ := db.Query(sql)` drops the error",
        path="handler.go", line=2,
    ))

    def _job(self, ro, tmp_path, prior):
        wt = tmp_path / "wt"
        wt.mkdir()
        git_out(wt, "init", "-q", "-b", "main")
        git_out(wt, "config", "user.email", "test@example.com")
        git_out(wt, "config", "user.name", "Test")
        git_out(wt, "config", "commit.gpgsign", "false")
        git_out(wt, "config", "core.hooksPath", str(tmp_path / "hooks"))
        (wt / "handler.go").write_text(self.BEFORE)
        git_out(wt, "add", "-A")
        git_out(wt, "commit", "-qm", "before")
        prior_sha = git_out(wt, "rev-parse", "HEAD").strip()

        job = TestPhaseSynthesis()._make_job(ro, tmp_path)
        job.wt_path = str(wt)
        job.prior = StoredReview(prior, ReviewMeta(head_sha=prior_sha))
        return job

    def _extras(self, ro, tmp_path, monkeypatch, merged, prior=None):
        job = self._job(ro, tmp_path, self.PRIOR if prior is None else prior)
        seen = {}

        def capture(phase, j, **extra):
            seen.update(extra)
            return "mock prompt"

        def mock_invoke(inv, **kwargs):
            Path(inv.output_path).write_text(
                json.dumps({"summary": "ok.", "verdict": "Approve."}))
            Path(inv.session_log).write_text("")
            return 0

        TestPhaseSynthesis._patch_pipeline(
            monkeypatch, build_prompt=capture, run_agent=mock_invoke,
        )
        ro._phase_synthesis(job, "", 3, Merged(merged))
        return seen

    def test_a_finding_no_group_mentioned_is_handed_to_synthesis(
        self, ro, tmp_path, monkeypatch,
    ):
        extras = self._extras(
            ro, tmp_path, monkeypatch,
            record(finding("M9", "x", path="other.go")),
        )
        assert [f.ref.finding_id for f in extras["unaccounted_prior"]] == ["M1"]

    def test_a_finding_the_groups_settled_is_not_asked_about_again(
        self, ro, tmp_path, monkeypatch,
    ):
        merged = record(ledger=(
            LedgerLine("M1", PriorDisposition.FIXED, "handler.go"),
        ))
        assert self._extras(ro, tmp_path, monkeypatch, merged)["unaccounted_prior"] == []

    def test_a_prior_review_that_reported_nothing_hands_over_nothing(
        self, ro, tmp_path, monkeypatch,
    ):
        assert self._extras(
            ro, tmp_path, monkeypatch, record(), prior=record(),
        )["unaccounted_prior"] == []
