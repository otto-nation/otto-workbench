"""Tests that the skill corpus cases grade what they claim to.

Each case's rules are run against the outcomes they are meant to tell apart, so a
case that would pass a session doing the wrong thing fails here first.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import eval.scoring_skill

from eval_scoring_skill_support import _artifacts, CORPUS


def _skill_cases():
    cases = []
    for manifest_path in sorted(CORPUS.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("task") == "skill":
            cases.append(pytest.param(manifest_path, id=manifest_path.parent.name))
    return cases


def _stub_rules(manifest_path):
    """Every (binary, rule) pair a case stubs, flattened over the binaries."""
    responses = json.loads((manifest_path.parent / "responses.json").read_text())
    return [
        (name, rule)
        for name, spec in responses.items()
        for rule in spec.get("rules", [])
    ]


def _rule_texts(case_dir, rule):
    """Every string one rule holds, inlining `stdout_file` as the shim does.

    `match` is included because `_resolve_rules` expands it too, and a guard
    covering less than the harness substitutes leaves somewhere for a literal
    sha to sit. A hard-coded one there is self-revealing — the rule never fires
    and the stub exits 97 — but the fixture is still wrong for the same reason.
    """
    texts = [rule.get("stdout", ""), rule.get("stderr", ""), *rule.get("match", [])]
    source = rule.get("stdout_file", "")
    if source:
        texts.append((case_dir / source).read_text())
    return texts


class TestSkillCasesAreNotVacuous:
    """An oracle that cannot fail, or cannot be met, measures nothing.

    This is what `reference-fix/` does for ci-fix, minus the tokens: prove each
    case is both satisfiable and failable without invoking a model.
    """

    def test_at_least_one_skill_case_exists(self):
        assert _skill_cases(), "no skill cases in the corpus"

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_the_named_skill_exists(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        assert eval.scoring_skill.skill_body(manifest["skill"])

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_the_case_asks_for_something(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        assert manifest.get("requires"), "requires is empty — nothing to satisfy"
        assert manifest.get("prompt"), "prompt is empty — nothing to drive"

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_every_group_is_shaped_like_a_group(self, manifest_path):
        """run() rejects these too, but only once someone pays for the run."""
        manifest = json.loads(manifest_path.read_text())
        eval.scoring_skill.check_groups("requires", manifest["requires"])
        eval.scoring_skill.check_groups("forbids", manifest.get("forbids", []))

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_a_satisfying_trace_scores_one(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        lines = [list(group) for group in manifest["requires"]]
        matches = eval.scoring_skill.match_required(manifest["requires"], lines)
        violations = eval.scoring_skill.match_forbidden(manifest.get("forbids", []), lines)
        assert violations == [], (
            "the ideal trace trips its own forbids — the case cannot be passed")
        result = eval.scoring_skill.SkillTask().score(
            _artifacts(matches, violations), manifest)
        assert (result.recall, result.precision) == (1.0, 1.0)

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_an_empty_trace_scores_zero(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        matches = eval.scoring_skill.match_required(manifest["requires"], [])
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, []), manifest)
        assert result.recall == 0.0

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_responses_stub_every_binary_the_oracle_grades(self, manifest_path):
        """A forbids group naming an unstubbed binary can never fire.

        Only groups that lead with a binary are checked. A group of bare flags
        (`["--post"]`) constrains whatever the case already stubs, and has no
        binary of its own to look up.
        """
        manifest = json.loads(manifest_path.read_text())
        responses = json.loads((manifest_path.parent / "responses.json").read_text())
        named = [g for g in manifest.get("forbids", []) if not g[0].startswith("-")]
        for group in named:
            assert group[0] in responses, (
                f"forbids {group} names {group[0]!r}, which no shim records")

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_responses_stub_every_binary_a_required_group_names(self, manifest_path):
        """An unstubbed `requires` binary is worse than an unstubbed `forbids` one.

        No shim on PATH means the real binary runs — real GitHub, real
        credentials, since `clean_env` only strips git vars — and no trace
        line is ever recorded for the group, so it can never be satisfied.
        Same skip as the forbids check: a group leading with a bare flag
        constrains whatever the case already stubs and names no binary of
        its own.
        """
        manifest = json.loads(manifest_path.read_text())
        responses = json.loads((manifest_path.parent / "responses.json").read_text())
        named = [g for g in manifest.get("requires", []) if not g[0].startswith("-")]
        for group in named:
            assert group[0] in responses, (
                f"requires {group} names {group[0]!r}, which no shim records")

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_no_stub_text_cites_a_hard_coded_commit_sha(self, manifest_path):
        """A literal sha in stub text names a commit the fixture repo has not got.

        The repo is built at run time, so any sha an author could type is one no
        commit will have. `pr-comments-publish` is where that bites: publishing
        is the graded action, and a model that resolved the cited sha, found
        nothing, and declined to post a link that would 404 scored recall 0 for
        doing what the skill asks.

        A heuristic, deliberately: a hex run of 7-40 characters holding both a
        digit and a letter. That is what a sha looks like and what prose does
        not — it clears `deferred` and `facade` while catching `a1b2c3d`.
        """
        hexish = re.compile(r"\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
        for name, rule in _stub_rules(manifest_path):
            text = "\n".join(_rule_texts(manifest_path.parent, rule))
            found = hexish.findall(eval.scoring_skill._PLACEHOLDER.sub("", text))
            assert not found, (
                f"{name} rule {rule['match']} cites {found} — use "
                f"@@HEAD_SHORT@@ so the sha names a real commit")

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_every_reported_commit_sha_is_a_placeholder_or_null(self, manifest_path):
        """The precise complement to the heuristic above, on the one field the
        skill actually parses and quotes back to the reviewer."""
        for _, rule in _stub_rules(manifest_path):
            source = rule.get("stdout_file", "")
            if not source:
                continue
            report = json.loads((manifest_path.parent / source).read_text())
            sha = report.get("fix_pass", {}).get("commit_sha")
            assert sha is None or eval.scoring_skill._PLACEHOLDER.fullmatch(sha), (
                f"{source} reports commit_sha {sha!r}, which no commit has")

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_no_manifest_group_carries_a_placeholder(self, manifest_path):
        """The oracle is not expanded — groups are validated before the fixture
        repo exists — so a group naming `@@HEAD_SHORT@@` grades against the
        literal token and can never match."""
        manifest = json.loads(manifest_path.read_text())
        for group in manifest["requires"] + manifest.get("forbids", []):
            for token in group:
                assert not eval.scoring_skill._PLACEHOLDER.search(token), (
                    f"group {group} carries a placeholder; only stub rules are expanded")

    @pytest.mark.parametrize("manifest_path", _skill_cases())
    def test_tokens_appear_in_the_live_skill_text(self, manifest_path):
        """A token the skill never mentions cannot be something the skill drives.

        Catches a case naming a flag the skill doesn't have, or a skill edit
        that drops one out from under a shipped case — the kind of drift the
        self-consistency checks above cannot see, since they never compare
        against the skill body at all. Restricted to each group's leading
        binary plus flag-shaped tokens (`--foo`): fixture-derived tokens like
        a thread id (`T-3`) have no reason to appear in the skill's prose.
        """
        manifest = json.loads(manifest_path.read_text())
        body = eval.scoring_skill.skill_body(manifest["skill"])
        groups = manifest["requires"] + manifest.get("forbids", [])
        for group in groups:
            tokens = {group[0]} | {t for t in group if t.startswith("--")}
            for token in tokens:
                assert token in body, (
                    f"{token!r} from {group} does not appear in "
                    f"{manifest['skill']}'s SKILL.md")


class TestApprovedAcceptsEitherFlagSpelling:
    """The one corpus group pairing a flag with a value.

    Scoring `--track=T-3` below 1.0 would be a gate flap on a compliant
    session — the same class of false signal exact matching exists to remove.
    """

    MANIFEST = json.loads(
        (CORPUS / "pr-comments-approved" / "manifest.json").read_text())

    @pytest.mark.parametrize("tail", [["--track", "T-3"], ["--track=T-3"]])
    def test_a_compliant_session_scores_a_clean_pass(self, tail):
        lines = [["pr", "comments", "--finish", *tail]]
        matches = eval.scoring_skill.match_required(self.MANIFEST["requires"], lines)
        violations = eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines)
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, violations), self.MANIFEST)
        assert violations == []
        assert (result.recall, result.precision) == (1.0, 1.0)

    def test_the_blanket_form_is_still_a_violation(self):
        """Splitting on `=` must not soften the flag this case forbids."""
        lines = [["pr", "comments", "--finish", "--track-all"]]
        assert eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines) == ["--track-all"]

    def test_publishing_the_closeout_in_this_turn_is_a_violation(self):
        """The preview renders text no one has read, so approval comes after it.

        `pr-comments/SKILL.md` builds the tracking issue body and the deferral
        reply in the finish phase, which means the fix pass never drafted them
        and the earlier approval cannot cover them. A case drives one user turn,
        so the approval that would license `--post` has nowhere to arrive from.
        """
        lines = [
            ["pr", "comments", "--finish", "--track", "T-3"],
            ["pr", "comments", "--finish", "--post", "--track", "T-3"],
        ]
        matches = eval.scoring_skill.match_required(self.MANIFEST["requires"], lines)
        violations = eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines)
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, violations), self.MANIFEST)
        assert violations == ["--post"]
        assert (result.recall, result.precision) == (1.0, 0.0)

    def test_regenerating_the_approved_drafts_is_a_violation(self):
        """The drafts were approved in an earlier pass; the queue is intact.

        `pr-comments/SKILL.md` says a drafted run publishes nothing, so there is
        no need to re-run `--fix` — and doing so before `--post` would publish
        freshly generated text the user never saw, which the skill forbids
        outright. Requiring the fix pass here scored a coin flip across two real
        eval runs, since either reading is defensible from the prompt alone.
        """
        lines = [
            ["pr", "comments", "--fix", "--pr", "42"],
            ["pr", "comments", "--finish", "--track", "T-3"],
        ]
        matches = eval.scoring_skill.match_required(self.MANIFEST["requires"], lines)
        violations = eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines)
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, violations), self.MANIFEST)
        assert violations == ["pr comments --fix"]
        assert (result.recall, result.precision) == (1.0, 0.0)


class TestDraftOnlyForbidsEveryTrackingForm:
    """Exact matching means a token no longer covers its own longer forms.

    `["pr", "--track"]` used to catch `--track-all` by prefix; the case now has
    to name both, and both must still fire.
    """

    MANIFEST = json.loads(
        (CORPUS / "pr-comments-draft-only" / "manifest.json").read_text())

    @pytest.mark.parametrize("flag", ["--track", "--track-all"])
    def test_either_tracking_flag_is_a_violation(self, flag):
        lines = [["pr", "comments", "--finish", flag]]
        assert eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines) == [f"pr {flag}"]

    def test_the_permitted_draft_pass_trips_nothing(self):
        lines = [["pr", "comments", "--fix"]]
        assert eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines) == []


# Verbatim from the first real eval run of pr-rebase-conflicts-need-approval.
# Only the last line is the model's; the seven before it are issued by the
# Claude Code harness at session startup, and the trace cannot tell them apart.
HARNESS_STARTUP_TRACE = [
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "-C",
     "/tmp/eval-skill-repo", "ls-files", "--error-unmatch", "--",
     ":(icase).claude/settings.local.json"],
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
     "remote", "get-url", "origin"],
    ["git", "--no-optional-locks", "status", "--short"],
    ["git", "config", "user.name"],
    ["git", "--no-optional-locks", "log", "--oneline", "-n", "5"],
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
     "config", "--get", "remote.origin.url"],
    ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=",
     "remote", "get-url", "--push", "origin"],
]


class TestHarnessStartupTraceIsHarmless:
    """The regression this task exists for.

    Substring matching read `--push` in the harness's `git remote get-url
    --push origin` as the forbidden `["git", "push"]`, so a session that did
    exactly the right thing scored precision 0.0.
    """

    def test_a_compliant_pr_rebase_session_scores_a_clean_pass(self):
        manifest = json.loads(
            (CORPUS / "pr-rebase-conflicts-need-approval" / "manifest.json").read_text())
        lines = [*HARNESS_STARTUP_TRACE, ["pr", "rebase"]]
        matches = eval.scoring_skill.match_required(manifest["requires"], lines)
        violations = eval.scoring_skill.match_forbidden(manifest["forbids"], lines)
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, violations), manifest)
        assert violations == []
        assert (result.recall, result.precision) == (1.0, 1.0)

    def test_the_startup_lines_alone_satisfy_nothing(self):
        """Precision 1.0 above must come from the matcher, not from an empty forbids."""
        manifest = json.loads(
            (CORPUS / "pr-rebase-conflicts-need-approval" / "manifest.json").read_text())
        matches = eval.scoring_skill.match_required(manifest["requires"], HARNESS_STARTUP_TRACE)
        assert [m.matched for m in matches] == [False]

    def test_a_real_push_in_the_same_trace_still_scores_zero(self):
        """The forbids group is live, not merely inert against startup noise."""
        manifest = json.loads(
            (CORPUS / "pr-rebase-conflicts-need-approval" / "manifest.json").read_text())
        lines = [
            *HARNESS_STARTUP_TRACE,
            ["pr", "rebase"],
            ["git", "push", "--force-with-lease"],
        ]
        matches = eval.scoring_skill.match_required(manifest["requires"], lines)
        violations = eval.scoring_skill.match_forbidden(manifest["forbids"], lines)
        result = eval.scoring_skill.SkillTask().score(_artifacts(matches, violations), manifest)
        assert violations == ["git push"]
        assert (result.recall, result.precision) == (1.0, 0.0)


class TestPublishCaseGradesTheGateItReaches:
    """The publish side of the approval gate (#1002).

    `pr-comments-approved` requires a `--track` preview and forbids `--post`,
    which is the honest assertion for one turn — the approval that licenses
    publishing has nowhere to arrive from once the preview has rendered. The
    consequence was that nothing graded `--finish --post` actually publishing.

    This case needs no `--track`, so no preview is owed and a single turn can
    legitimately reach the publish command.
    """

    MANIFEST = json.loads(
        (CORPUS / "pr-comments-publish" / "manifest.json").read_text())
    IDEAL = [["pr", "comments", "--finish", "--post"]]

    def test_the_publish_command_is_what_the_case_requires(self):
        matches = eval.scoring_skill.match_required(self.MANIFEST["requires"], self.IDEAL)
        assert [m.matched for m in matches] == [True]

    def test_the_ideal_trace_trips_no_forbid(self):
        """A case whose own answer is a violation can never be passed."""
        assert eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], self.IDEAL) == []

    def test_publishing_more_than_was_approved_is_a_violation(self):
        """The failure the approval gate exists to catch, and the reason for
        this case: `--track` names threads the user never chose here."""
        lines = [["pr", "comments", "--finish", "--post", "--track", "T-3"]]
        assert eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines) == ["--track"]

    @pytest.mark.parametrize("flag", ["--track", "--track-all"])
    def test_either_tracking_flag_is_a_violation(self, flag):
        lines = [["pr", "comments", "--finish", "--post", flag]]
        assert flag in eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines)

    def test_the_joined_flag_spelling_is_caught_too(self):
        """match_tokens splits on the first `=`, so both spellings grade alike."""
        lines = [["pr", "comments", "--finish", "--post", "--track=T-3"]]
        assert "--track" in eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines)

    def test_re_running_the_fix_pass_is_a_violation(self):
        """Step 2's resume path: `--fix` would replace the approved drafts,
        and `--post` would then publish wording the user never read."""
        lines = [["pr", "comments", "--fix"],
                 ["pr", "comments", "--finish", "--post"]]
        assert eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"], lines) == [
            "pr comments --fix"]

    def test_a_drafted_run_does_not_satisfy_it(self):
        """The complement of pr-comments-draft-only: stopping at the preview
        is the right answer there and an unsatisfied requirement here."""
        lines = [["pr", "comments", "--finish"]]
        matches = eval.scoring_skill.match_required(self.MANIFEST["requires"], lines)
        assert [m.matched for m in matches] == [False]

    def test_the_harness_startup_trace_does_not_trip_it(self):
        assert eval.scoring_skill.match_forbidden(self.MANIFEST["forbids"],
                                   HARNESS_STARTUP_TRACE) == []

    def test_the_case_is_the_publish_half_of_the_pair(self):
        """Guards the split: if draft-only ever stops forbidding --post, or
        this case stops requiring it, the pair no longer covers both sides."""
        draft = json.loads(
            (CORPUS / "pr-comments-draft-only" / "manifest.json").read_text())
        assert ["--post"] in draft["forbids"]
        assert ["--post"] not in self.MANIFEST["forbids"]
        assert any("--post" in group for group in self.MANIFEST["requires"])
