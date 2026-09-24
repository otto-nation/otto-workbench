"""The command / domain / phase join — the check the three declarations never had.

`validate_needs` is the one check among three things that must agree: every
`pr` subcommand, every `PhaseDomain`, every `PRState` domain field. Folding
dispatch into `CommandSpec` is not enough; only a test can see across the
three modules. This is that test.

It pins the *measured* partition, not a 1:1 ideal. Five of nine subcommands
delegate and those five are the five `PhaseDomain` members; the other four
have no phase domain. `COMMENTS` owns three state fields. `push` and
`supersession` have state and no subcommand.

One clause of the join is deliberately absent. `CommandSpec.handler` lands in
T7 commit 4a on a sibling branch, and "every handler path imports" is asserted
there, beside the field — `tests/test_cli_registry.py`. It moves here once both
have landed and the two `None` entries (`review`, `comments`, whose wrappers are
still binary-local) are filled by 4c, because an exemption list is the thing
this check exists not to have.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from agent.registry import PHASES  # noqa: E402
from cli.registry import COMMANDS  # noqa: E402
from core.phases import PhaseDomain  # noqa: E402
from pr.state import _domains  # noqa: E402


# ── the measured partition, pinned as literals ────────────────────────────
#
# Read off COMMANDS / PHASES / PRState these would track whatever those
# modules say, which is the one thing a drift would change. The facts below
# were counted against the tree; a change in any of them is a deliberate
# edit here.

# The five PhaseDomain values. Same names as the five delegating subcommands
# — that equality *is* the join, and it is not an accident of spelling.
PHASE_DOMAINS = frozenset({"review", "comments", "ci", "rebase", "describe"})

DELEGATES = {
    "ci": "ci-check",
    "review": "claude-review",
    "comments": "review-threads",
    "rebase": "pr-rebase",
    "describe": "pr-describe",
}

# status / fix / gc / create have no PhaseDomain. They run inside `pr` and
# do not own a phase inventory. `fix` the command is not `fix` the state
# field: the command runs ci + review + comments, the field is comments-fix.
COMMANDS_WITHOUT_PHASE_DOMAIN = frozenset({"create", "status", "fix", "gc"})

ALL_COMMANDS = frozenset({
    "create", "status", "ci", "review", "comments",
    "fix", "rebase", "describe", "gc",
})

# Nine PRState domain fields. COMMENTS owns three of them; push and
# supersession have state and no subcommand.
PRSTATE_DOMAINS = frozenset({
    "ci", "review", "comments", "triage", "fix",
    "rebase", "push", "describe", "supersession",
})
COMMENTS_EXTRA_FIELDS = frozenset({"triage", "fix"})
STATE_WITHOUT_COMMAND = frozenset({"push", "supersession"})


# ── 1. every CommandSpec.script names a real executable ───────────────────


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_every_delegated_script_exists_and_is_executable_under_ai_bin():
    """A name in the registry that nothing on disk answers is a dispatch
    that cannot run. `script` is a name, not a path — the entry point joins
    it to its own `ai/bin` — so the file that has to exist is that join.
    """
    for name, script in DELEGATES.items():
        assert COMMANDS[name].script == script, name
        path = BIN_DIR / script
        assert path.is_file(), f"{script} is not a file under ai/bin"
        assert os.access(path, os.X_OK), f"{script} is not executable"


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_a_command_with_no_phase_domain_names_no_script():
    """The four in-process commands are the four with no PhaseDomain.

    Measured, not idealised: a fifth in-process command would still need
    accounting on the PhaseDomain side, and a delegate without a domain
    would be a script nobody in the phase inventory can claim.
    """
    for name in COMMANDS_WITHOUT_PHASE_DOMAIN:
        assert COMMANDS[name].script is None, name


# ── 2. every PhaseDomain is reachable, and the rest is accounted for ──────


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_every_phase_domain_is_reachable_from_a_command():
    """A PhaseDomain with no subcommand is an entry point nothing dispatches.

    Reachable means the domain's value *is* a subcommand name. That is the
    real join today — CommandSpec has no `domain` field — so a domain added
    under a different spelling would pass `PhaseDomain` and still be
    unreachable from `pr`.
    """
    assert {d.value for d in PhaseDomain} == PHASE_DOMAINS
    assert PHASE_DOMAINS <= set(COMMANDS)


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_commands_with_no_phase_domain_are_explicitly_accounted_for():
    """create, status, fix, gc are not missing domains; they are the set
    that does not own one. An unlisted ninth command would be the missing
    case this used to be unable to see.
    """
    assert set(COMMANDS) == ALL_COMMANDS
    assert set(COMMANDS) - {d.value for d in PhaseDomain} \
        == COMMANDS_WITHOUT_PHASE_DOMAIN


# ── 3. every PhaseSpec.domain is a real PhaseDomain ───────────────────────


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_every_phase_spec_domain_is_a_real_phase_domain():
    """PHASES is a different declaration from the enum. A spec that stored
    the string `"review"` would compare equal to `PhaseDomain.REVIEW` and
    still not *be* one — `REVIEW_PHASES` filters on identity.
    """
    for phase, spec in PHASES.items():
        assert isinstance(spec.domain, PhaseDomain), phase


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_every_phase_domain_has_at_least_one_phase():
    """The inverse of the spec-side check: a PhaseDomain nobody lists is
    an entry point with an empty inventory, which is how a domain added
    for a future command quietly ships dead.
    """
    assert {s.domain for s in PHASES.values()} == set(PhaseDomain)


# ── 4. PRState fields and PhaseDomain members, as they actually line up ───


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_prstate_fields_cover_every_phase_domain_and_the_measured_rest():
    """Five PhaseDomain members, nine PRState fields. The gap is not drift.

    COMMENTS owns three fields (`comments`, `triage`, `fix`). `push` and
    `supersession` have state and no subcommand. The remaining four domains
    are 1:1 with a field of the same name. Adding a tenth field without
    updating this partition is the failure `_validate_needs` never covered.
    """
    assert set(_domains()) == PRSTATE_DOMAINS
    assert set(_domains()) - {d.value for d in PhaseDomain} \
        == COMMENTS_EXTRA_FIELDS | STATE_WITHOUT_COMMAND


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_push_and_supersession_have_state_and_no_subcommand():
    """They are refreshed or cached by whoever runs first, not dispatched
    as `pr push` / `pr supersession`. A command added under either name
    would own a field that is today written as a side effect, and this
    is the line that would notice.
    """
    for name in STATE_WITHOUT_COMMAND:
        assert name in _domains()
        assert name not in COMMANDS


# passes-at-base: pins the join this change does not alter; the three declarations already agree at base
def test_comments_owns_three_state_fields():
    """The design-spec join: COMMENTS alone owns three. `triage` has no
    subcommand of its own (it runs under `pr comments`). `fix` the field
    is the comments-fix summary, which is not `fix` the command.
    """
    for name in COMMENTS_EXTRA_FIELDS:
        assert name in _domains()
    assert "triage" not in COMMANDS
    assert "fix" in COMMANDS
    assert COMMANDS["fix"].script is None
    assert "fix" not in {d.value for d in PhaseDomain}
